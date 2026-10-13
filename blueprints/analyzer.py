import csv
import io
import json
from datetime import datetime, timedelta

import pytz
from flask import (
    Blueprint,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import desc, func

from database.analyzer_db import AnalyzerLog, db_session
from sandbox.round_trips import (
    attach_running_totals,
    attach_trips_to_requests,
    drop_entry_rows_when_closed,
    pair_round_trips,
    serialize_trip,
    summarize_round_trips,
)
from utils.api_analyzer import get_analyzer_stats
from utils.logging import get_logger
from utils.session import check_session_validity

logger = get_logger(__name__)

analyzer_bp = Blueprint("analyzer_bp", __name__, url_prefix="/analyzer")


def _extract_fill_price(request_data, response_data):
    """Best fill/LTP available on an analyzer log (request first, then response)."""
    blobs = []
    if isinstance(response_data, dict):
        blobs.append(response_data)
        data = response_data.get("data")
        if isinstance(data, dict):
            blobs.append(data)
    if isinstance(request_data, dict):
        blobs.append(request_data)
    for blob in blobs:
        for key in ("average_price", "price", "ltp"):
            raw = blob.get(key)
            try:
                price = float(raw)
            except (TypeError, ValueError):
                continue
            if price > 0:
                return round(price, 2)
    return None


def _date_bounds(start_date=None, end_date=None):
    """Resolve inclusive date bounds; default to today IST when both are empty."""
    ist = pytz.timezone("Asia/Kolkata")
    if isinstance(start_date, str) and start_date:
        start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
    if isinstance(end_date, str) and end_date:
        end_date = datetime.strptime(end_date, "%Y-%m-%d").date()
    if not start_date and not end_date:
        today_ist = datetime.now(ist).date()
        start_date = today_ist
        end_date = today_ist
    return start_date, end_date


def get_sandbox_round_trips(start_date=None, end_date=None):
    """FIFO round-trips from sandbox fills for the analyzer date filter."""
    try:
        from datetime import time as dt_time

        from database.sandbox_db import SandboxTrades
        from database.token_db import get_symbol_info

        start_date, end_date = _date_bounds(start_date, end_date)
        query = SandboxTrades.query
        if start_date:
            query = query.filter(
                SandboxTrades.trade_timestamp >= datetime.combine(start_date, dt_time.min)
            )
        if end_date:
            query = query.filter(
                SandboxTrades.trade_timestamp <= datetime.combine(end_date, dt_time.max)
            )
        rows = query.order_by(SandboxTrades.trade_timestamp.asc()).all()
        fills = [
            {
                "symbol": row.symbol,
                "exchange": row.exchange,
                "product": row.product,
                "action": row.action,
                "quantity": row.quantity,
                "price": float(row.price),
                "timestamp": row.trade_timestamp,
                "tradeid": row.tradeid,
            }
            for row in rows
        ]
        trips = pair_round_trips(fills)
        serialized = []
        for trip in trips:
            contract_value = 1.0
            try:
                info = get_symbol_info(trip.get("symbol"), trip.get("exchange"))
                if info and info.contract_value:
                    contract_value = float(info.contract_value)
            except Exception:
                contract_value = 1.0
            serialized.append(serialize_trip(trip, contract_value=contract_value))
        serialized = attach_running_totals(serialized)
        serialized.sort(key=lambda item: item.get("exit_time") or item.get("entry_time") or "")
        serialized.reverse()
        return serialized
    except Exception:
        logger.exception("Error building sandbox round-trips for analyzer")
        return []


def format_request(req, ist):
    """Format a single request entry"""
    try:
        request_data = (
            json.loads(req.request_data) if isinstance(req.request_data, str) else req.request_data
        )
        response_data = (
            json.loads(req.response_data)
            if isinstance(req.response_data, str)
            else req.response_data
        )

        # Base request info
        formatted_request = {
            "timestamp": req.created_at.astimezone(ist).strftime("%Y-%m-%d %H:%M:%S"),
            "api_type": req.api_type,
            "source": request_data.get("strategy", "Unknown"),
            "request_data": request_data,
            "response_data": response_data,  # Include complete response data
            "analysis": {
                "issues": response_data.get("status") == "error",
                "error": response_data.get("message"),
                "error_type": "error" if response_data.get("status") == "error" else "success",
                "warnings": response_data.get("warnings", []),
            },
        }

        # Add fields based on API type
        if req.api_type in ["placeorder", "placesmartorder"]:
            formatted_request.update(
                {
                    "symbol": request_data.get("symbol", "Unknown"),
                    "exchange": request_data.get("exchange", "Unknown"),
                    "action": request_data.get("action", "Unknown"),
                    "quantity": request_data.get("quantity", 0),
                    "price_type": request_data.get("pricetype", "Unknown"),
                    "product_type": request_data.get("product", "Unknown"),
                    "fill_price": _extract_fill_price(request_data, response_data),
                }
            )
            if req.api_type == "placesmartorder":
                formatted_request["position_size"] = request_data.get("position_size", 0)
        elif req.api_type == "closeposition":
            response_blob = response_data if isinstance(response_data, dict) else {}
            formatted_request.update(
                {
                    "symbol": request_data.get("symbol") or response_blob.get("symbol") or "",
                    "exchange": request_data.get("exchange") or response_blob.get("exchange") or "",
                    "action": request_data.get("action") or "CLOSE",
                    "quantity": request_data.get("quantity") or 0,
                    "product_type": request_data.get("product")
                    or request_data.get("product_type")
                    or "",
                    "fill_price": _extract_fill_price(request_data, response_data),
                }
            )
        elif req.api_type == "cancelorder":
            formatted_request.update({"orderid": request_data.get("orderid", "Unknown")})

        return formatted_request
    except Exception as e:
        logger.exception(f"Error formatting request {req.id}: {str(e)}")
        return None


def get_recent_requests():
    """Get recent analyzer requests"""
    try:
        ist = pytz.timezone("Asia/Kolkata")
        recent = AnalyzerLog.query.order_by(AnalyzerLog.created_at.desc()).limit(100).all()
        requests = []

        for req in recent:
            formatted = format_request(req, ist)
            if formatted:
                requests.append(formatted)

        return requests
    except Exception as e:
        logger.exception(f"Error getting recent requests: {str(e)}")
        return []


def get_filtered_requests(start_date=None, end_date=None):
    """Get analyzer requests with date filtering"""
    try:
        ist = pytz.timezone("Asia/Kolkata")
        query = AnalyzerLog.query

        # Apply date filters if provided
        if start_date:
            if isinstance(start_date, str):
                start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
            query = query.filter(func.date(AnalyzerLog.created_at) >= start_date)
        if end_date:
            if isinstance(end_date, str):
                end_date = datetime.strptime(end_date, "%Y-%m-%d").date()
            query = query.filter(func.date(AnalyzerLog.created_at) <= end_date)

        # If no dates provided, default to today
        if not start_date and not end_date:
            today_ist = datetime.now(ist).date()
            query = query.filter(func.date(AnalyzerLog.created_at) == today_ist)

        # Get results ordered by created_at
        results = query.order_by(AnalyzerLog.created_at.desc()).all()
        requests = []

        for req in results:
            formatted = format_request(req, ist)
            if formatted:
                requests.append(formatted)

        try:
            attach_trips_to_requests(requests, get_sandbox_round_trips(start_date, end_date))
            requests = drop_entry_rows_when_closed(requests)
        except Exception:
            logger.exception("Error attaching round-trips to analyzer requests")

        return requests
    except Exception as e:
        logger.exception(f"Error getting filtered requests: {e}")
        return []


def generate_csv(requests, trade_stats=None):
    """Generate CSV from analyzer requests"""
    try:
        output = io.StringIO()
        writer = csv.writer(output)

        if trade_stats:
            writer.writerow(["Trade performance"])
            writer.writerow(["Metric", "Value"])
            summary_rows = [
                ("Closed trades", trade_stats.get("trades")),
                ("Open trades", trade_stats.get("open_trades")),
                ("Cumulative P&L (INR)", trade_stats.get("net_pnl")),
                ("Cumulative RoI %", trade_stats.get("roi_percent")),
                ("Premium deployed", trade_stats.get("total_premium")),
                ("Winning", trade_stats.get("winning")),
                ("Losing", trade_stats.get("losing")),
                ("Even", trade_stats.get("even")),
                ("Win rate %", trade_stats.get("win_rate")),
                ("Gross profit", trade_stats.get("gross_profit")),
                ("Gross loss", trade_stats.get("gross_loss")),
                ("Average win", trade_stats.get("avg_win")),
                ("Average loss", trade_stats.get("avg_loss")),
                ("Expectancy", trade_stats.get("expectancy")),
                ("Average RoI %", trade_stats.get("avg_roi_percent")),
                (
                    "Profit factor",
                    "∞"
                    if trade_stats.get("profit_factor_infinite")
                    else trade_stats.get("profit_factor"),
                ),
                ("Best trade", trade_stats.get("best_trade")),
                ("Worst trade", trade_stats.get("worst_trade")),
                ("Max consecutive wins", trade_stats.get("max_consecutive_wins")),
                ("Max consecutive losses", trade_stats.get("max_consecutive_losses")),
                ("Max drawdown (INR)", trade_stats.get("max_drawdown")),
                ("Max drawdown %", trade_stats.get("max_drawdown_pct")),
                ("PE trades", trade_stats.get("pe_trades")),
                ("PE P&L", trade_stats.get("pe_pnl")),
                ("CE trades", trade_stats.get("ce_trades")),
                ("CE P&L", trade_stats.get("ce_pnl")),
            ]
            writer.writerows(summary_rows)
            writer.writerow([])

        # Write headers
        headers = [
            "Timestamp",
            "API Type",
            "Source",
            "Symbol",
            "Strike",
            "Exchange",
            "Action",
            "Quantity",
            "Entry Time",
            "Entry LTP",
            "Exit Time",
            "Exit LTP",
            "P&L",
            "% RoI",
            "Price Type",
            "Product Type",
            "Status",
            "Error Message",
        ]
        writer.writerow(headers)

        # Write data
        for req in requests:
            row = [
                req["timestamp"],
                req["api_type"],
                req["source"],
                req.get("symbol", ""),
                f"{req.get('strike') or ''} {req.get('option_type') or ''}".strip(),
                req.get("exchange", ""),
                req.get("action", ""),
                req.get("traded_quantity") or req.get("quantity", ""),
                req.get("entry_time", ""),
                req.get("entry_price", ""),
                req.get("exit_time", ""),
                req.get("exit_price", ""),
                req.get("trade_pnl", ""),
                req.get("roi_percent", ""),
                req.get("price_type", ""),
                req.get("product_type", ""),
                "Error" if req["analysis"]["issues"] else "Success",
                req["analysis"].get("error", ""),
            ]
            writer.writerow(row)

        return output.getvalue()
    except Exception as e:
        logger.exception(f"Error generating CSV: {str(e)}")
        return ""


@analyzer_bp.route("/")
@check_session_validity
def analyzer():
    """Render the analyzer dashboard"""
    try:
        # Get date parameters
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")

        # Get stats with proper structure
        stats = get_analyzer_stats()
        if not isinstance(stats, dict):
            stats = {
                "total_requests": 0,
                "sources": {},
                "symbols": [],
                "issues": {
                    "total": 0,
                    "by_type": {
                        "rate_limit": 0,
                        "invalid_symbol": 0,
                        "missing_quantity": 0,
                        "invalid_exchange": 0,
                        "other": 0,
                    },
                },
            }

        # Get filtered requests
        requests = get_filtered_requests(start_date, end_date)

        return render_template(
            "analyzer.html",
            requests=requests,
            stats=stats,
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as e:
        logger.exception(f"Error rendering analyzer: {str(e)}")
        flash("Error loading analyzer dashboard", "error")
        return redirect(url_for("core_bp.home"))


@analyzer_bp.route("/api/data")
@check_session_validity
def api_get_data():
    """API endpoint to get analyzer data (stats + requests) as JSON for React frontend"""
    try:
        # Get date parameters
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")

        # Get stats with proper structure
        stats = get_analyzer_stats()
        if not isinstance(stats, dict):
            stats = {
                "total_requests": 0,
                "sources": {},
                "symbols": [],
                "issues": {
                    "total": 0,
                    "by_type": {
                        "rate_limit": 0,
                        "invalid_symbol": 0,
                        "missing_quantity": 0,
                        "invalid_exchange": 0,
                        "other": 0,
                    },
                },
            }

        # Get filtered requests
        requests_data = get_filtered_requests(start_date, end_date)
        trades = get_sandbox_round_trips(start_date, end_date)

        # Transform stats for React frontend
        stats_transformed = {
            "total_requests": stats.get("total_requests", 0),
            "issues": stats.get("issues", {"total": 0}),
            "symbols": list(stats.get("symbols", []))
            if isinstance(stats.get("symbols"), (list, set))
            else [],
            "sources": list(stats.get("sources", {}).keys())
            if isinstance(stats.get("sources"), dict)
            else [],
        }

        return jsonify(
            {
                "status": "success",
                "data": {
                    "stats": stats_transformed,
                    "requests": requests_data,
                    "trades": trades,
                    "trade_stats": summarize_round_trips(trades),
                },
            }
        )
    except Exception as e:
        logger.exception(f"Error getting analyzer data: {str(e)}")
        return jsonify(
            {"status": "error", "message": f"Error loading analyzer data: {str(e)}"}
        ), 500


@analyzer_bp.route("/stats")
@check_session_validity
def get_stats():
    """Get analyzer stats endpoint"""
    try:
        stats = get_analyzer_stats()
        return jsonify(stats)
    except Exception as e:
        logger.exception(f"Error getting analyzer stats: {str(e)}")
        return jsonify(
            {
                "total_requests": 0,
                "sources": {},
                "symbols": [],
                "issues": {
                    "total": 0,
                    "by_type": {
                        "rate_limit": 0,
                        "invalid_symbol": 0,
                        "missing_quantity": 0,
                        "invalid_exchange": 0,
                        "other": 0,
                    },
                },
            }
        ), 500


@analyzer_bp.route("/requests")
@check_session_validity
def get_requests():
    """Get analyzer requests endpoint"""
    try:
        requests = get_recent_requests()
        return jsonify({"requests": requests})
    except Exception as e:
        logger.exception(f"Error getting analyzer requests: {str(e)}")
        return jsonify({"requests": []}), 500


@analyzer_bp.route("/clear")
@check_session_validity
def clear_logs():
    """Clear analyzer logs"""
    try:
        # Delete all logs older than 24 hours
        cutoff = datetime.now(pytz.UTC) - timedelta(hours=24)
        AnalyzerLog.query.filter(AnalyzerLog.created_at < cutoff).delete()
        db_session.commit()
        flash("Analyzer logs cleared successfully", "success")
    except Exception as e:
        logger.exception(f"Error clearing analyzer logs: {str(e)}")
        flash("Error clearing analyzer logs", "error")

    return redirect(url_for("analyzer_bp.analyzer"))


@analyzer_bp.route("/export", methods=["GET"])
@check_session_validity
def export_requests():
    """Export analyzer requests to CSV"""
    try:
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")

        # Get filtered requests
        requests = get_filtered_requests(start_date, end_date)
        trades = get_sandbox_round_trips(start_date, end_date)

        # Generate CSV
        csv_data = generate_csv(requests, summarize_round_trips(trades))

        # Create the response
        output = Response(csv_data, mimetype="text/csv")
        output.headers["Content-Disposition"] = (
            f"attachment; filename=analyzer_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )
        return output
    except Exception as e:
        logger.exception(f"Error exporting requests: {str(e)}")
        flash("Error exporting requests", "error")
        return redirect(url_for("analyzer_bp.analyzer"))
