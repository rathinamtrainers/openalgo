"""BTC option desk monitor.

GET /btc/api/desk
    Live book status and recent paper/live arrests from the local desk.
"""

from flask import Blueprint, jsonify, session
from flask_cors import cross_origin

from services.btc_desk_service import read_desk
from utils.logging import get_logger

logger = get_logger(__name__)

btc_desk_bp = Blueprint("btc_desk_bp", __name__, url_prefix="/")


@btc_desk_bp.route("/btc/api/desk", methods=["GET"])
@cross_origin()
def btc_desk():
    """OpenAlgo sign-in is enough. This book is Delta, not the Zerodha session."""
    try:
        if not session.get("user"):
            return jsonify({"status": "error", "message": "Authentication required"}), 401
        return jsonify(read_desk())
    except Exception as exc:
        logger.exception(f"Error in BTC desk API: {exc}")
        return (
            jsonify({"status": "error", "message": "An error occurred processing your request"}),
            500,
        )
