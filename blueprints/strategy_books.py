"""Session-authenticated My Strategies book list. Read-only."""

from flask import Blueprint, jsonify, request

from limiter import limiter
from services.desk_decisions_service import list_decisions
from services.strategy_books_service import list_strategy_books
from services.strategy_timeline_service import list_timeline
from utils.session import check_session_validity

strategy_books_bp = Blueprint("strategy_books_bp", __name__)


@strategy_books_bp.route("/strategy/api/books", methods=["GET"])
@check_session_validity
@limiter.limit("60/minute")
def get_strategy_books():
    return jsonify({"status": "success", "books": list_strategy_books()})


@strategy_books_bp.route("/strategy/api/books/<book_id>/decisions", methods=["GET"])
@check_session_validity
@limiter.limit("60/minute")
def get_book_decisions(book_id: str):
    if book_id != "analyzer-nifty":
        return jsonify({"status": "error", "message": "no decision log for this book"}), 404
    try:
        limit = int(request.args.get("limit", 200))
    except (TypeError, ValueError):
        limit = 200
    payload = list_decisions(
        day=request.args.get("day"),
        outcome=request.args.get("outcome"),
        limit=limit,
    )
    return jsonify({"status": "success", **payload})


@strategy_books_bp.route("/strategy/api/books/<book_id>/timeline", methods=["GET"])
@check_session_validity
@limiter.limit("60/minute")
def get_book_timeline(book_id: str):
    try:
        limit = int(request.args.get("limit", 200))
    except (TypeError, ValueError):
        limit = 200
    payload = list_timeline(
        book_id,
        day=request.args.get("day"),
        kind=request.args.get("kind"),
        limit=limit,
    )
    if payload.get("status") == "error":
        return jsonify(payload), 404
    return jsonify({"status": "success", **payload})
