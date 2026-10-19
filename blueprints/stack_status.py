"""Session-authenticated stack liveness for the dashboard strip."""

from flask import Blueprint, jsonify, session

from database.auth_db import get_auth_token
from database.settings_db import get_analyze_mode
from limiter import limiter
from services.stack_status_service import build_stack_status
from utils.session import check_session_validity

stack_status_bp = Blueprint("stack_status_bp", __name__)


@stack_status_bp.route("/health/api/stack", methods=["GET"])
@check_session_validity
@limiter.limit("60/minute")
def get_stack_status():
    username = session.get("user")
    broker = session.get("broker")
    token = get_auth_token(username) if username else None
    return jsonify(
        build_stack_status(
            analyze_mode=bool(get_analyze_mode()),
            broker=broker,
            broker_connected=bool(session.get("logged_in") and token),
        )
    )
