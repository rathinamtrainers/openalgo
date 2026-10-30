"""Greeks Lab blueprint — live chain Greeks, theta path, and what-if."""

import re

from flask import Blueprint, jsonify, request, session
from flask_cors import cross_origin

from database.auth_db import get_api_key_for_tradingview
from services.greeks_lab_coa import get_coa_boards
from services.greeks_lab_service import get_greeks_lab_data
from utils.logging import get_logger
from utils.session import check_session_validity

logger = get_logger(__name__)

greeks_lab_bp = Blueprint("greeks_lab_bp", __name__, url_prefix="/")


@greeks_lab_bp.route("/greekslab/api/coa", methods=["GET"])
@cross_origin()
@check_session_validity
def greeks_lab_coa():
    """Public CoA briefs for the unified Lab page. No Hands fields."""
    try:
        login_username = session.get("user")
        if not login_username:
            return jsonify({"status": "error", "message": "Authentication required"}), 401
        api_key = get_api_key_for_tradingview(login_username)
        return jsonify(get_coa_boards(api_key=api_key)), 200
    except Exception:
        logger.exception("Error in Greeks lab CoA board")
        return jsonify({"status": "error", "message": "Failed to load CoA board"}), 500


@greeks_lab_bp.route("/greekslab/api/snapshot", methods=["POST"])
@cross_origin()
@check_session_validity
def greeks_lab_snapshot():
    """Live Greeks plus a Black-76 next-hour projection."""
    try:
        login_username = session.get("user")
        if not login_username:
            return jsonify({"status": "error", "message": "Authentication required"}), 401

        api_key = get_api_key_for_tradingview(login_username)
        if not api_key:
            return jsonify(
                {
                    "status": "error",
                    "message": "API key not configured. Please generate an API key in /apikey",
                }
            ), 401

        data = request.get_json(silent=True) or {}
        underlying = data.get("underlying", "").strip()[:20]
        exchange = data.get("exchange", "").strip()[:20]
        expiry_date = data.get("expiry_date", "").strip()[:10]
        strike_raw = data.get("strike")
        horizon_raw = data.get("horizon_minutes", 60)

        if not underlying or not exchange or not expiry_date:
            return jsonify(
                {
                    "status": "error",
                    "message": "underlying, exchange, and expiry_date are required",
                }
            ), 400

        if not re.match(r"^[A-Z0-9]+$", underlying) or not re.match(r"^[A-Z0-9_]+$", exchange):
            return jsonify({"status": "error", "message": "Invalid input format"}), 400

        if not re.match(r"^\d{2}[A-Z]{3}\d{2}$", expiry_date):
            return jsonify(
                {"status": "error", "message": "Invalid expiry_date format. Expected DDMMMYY"}
            ), 400

        strike = None
        if strike_raw not in (None, ""):
            try:
                strike = float(strike_raw)
            except (TypeError, ValueError):
                return jsonify({"status": "error", "message": "Invalid strike"}), 400

        try:
            horizon_minutes = int(horizon_raw)
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "Invalid horizon_minutes"}), 400

        success, response, status_code = get_greeks_lab_data(
            underlying=underlying,
            exchange=exchange,
            expiry_date=expiry_date,
            api_key=api_key,
            strike=strike,
            horizon_minutes=horizon_minutes,
        )
        return jsonify(response), status_code
    except Exception as exc:
        logger.exception("Error in Greeks lab API: %s", exc)
        return (
            jsonify({"status": "error", "message": "An error occurred processing your request"}),
            500,
        )
