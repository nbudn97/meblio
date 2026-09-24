"""One-shot: split app.py method groups into api_orders / api_accounts / api_market mixins."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"
src = APP.read_text(encoding="utf-8")
lines = src.splitlines(keepends=True)

CONST_NAMES = ["TRUST_DEVICE_COOKIE", "TRUST_DEVICE_DAYS"]

# --- helpers at module level (verify_totp … validate_csrf_token) ---
HELPERS = [
    "verify_totp",
    "get_tfa_trust_secret",
    "make_trust_cookie",
    "verify_trust_cookie",
    "read_trusted_user_id",
    "create_pending_token",
    "generate_csrf_token",
    "validate_csrf_token",
]

# --- methods to extract ---
ORDERS = [
    "api_orders",
    "_order_participant_ids",
    "_require_order_access",
    "_seed_order_stages",
    "api_order_stages",
    "api_add_order_stage",
    "api_update_order_stage",
    "api_accept_order",
    "_warranty_date",
    "api_estimate",
    "api_list_proposals",
    "api_create_proposal",
    "api_update_proposal_status",
    "api_order_contract",
    "api_create_order",
    "api_create_response",
    "api_choose_maker",
    "api_cancel_order",
    "api_close_order",
    "api_publish_order",
    "_due_at",
    "api_duplicate_order",
    "api_maker_funnel",
    "api_check_deadlines",
    "run_deadline_reminders",
    "api_invite_to_quote",
    "api_export_excel",
]
ATTR_ORDERS = ["DEFAULT_ORDER_STAGES"]

ACCOUNTS = [
    "api_session",
    "api_register",
    "api_login",
    "api_logout",
    "api_profile",
    "api_favorites_list",
    "api_add_favorite",
    "api_remove_favorite",
    "api_tfa_status",
    "api_tfa_setup",
    "api_tfa_verify",
    "api_resend_verification",
    "api_verify_email",
    "api_tfa_login",
    "api_forgot_password",
    "api_reset_password",
    "api_change_password",
    "api_change_email",
    "api_delete_account",
    "api_auth_token",
    "api_csrf_token",
    "api_notifications_list",
    "api_notification_read",
    "api_notifications_read_all",
    "api_notification_preferences",
    "api_update_notification_preferences",
    "api_delete_notification",
    "api_notifications_clear_all",
    "_response_quota",
    "api_tariff_info",
    "api_tariff_upgrade",
    "api_link_messenger",
    "api_documents_list",
    "api_upload_document",
    "api_delete_document",
    "api_upload_logo",
]

MARKET = [
    "api_makers",
    "api_regions",
    "api_company_types",
    "api_companies",
    "api_company_detail",
    "api_services_list",
    "api_service_detail",
    "api_create_service",
    "api_update_service",
    "_save_service_params",
    "api_delete_service",
    "api_reviews_list",
    "api_create_review",
    "api_global_search",
    "api_articles_list",
    "api_article_detail",
    "api_admin_articles_list",
    "api_admin_article_save",
    "api_admin_article_delete",
    "api_upload_gallery",
    "api_delete_gallery_item",
    "api_threads",
    "api_messages",
    "api_upload_thread_file",
    "api_send_message",
    "api_create_report",
    "api_admin_hide_content",
    "api_maker_stats",
]


def extract_class_members(tree):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "MeblioHandler":
            members = {}
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    members[item.name] = (item.lineno, item.end_lineno, item)
                elif isinstance(item, ast.Assign):
                    for t in item.targets:
                        if isinstance(t, ast.Name):
                            members[t.id] = (item.lineno, item.end_lineno, item)
            return members
    raise SystemExit("MeblioHandler not found")


def extract_module_funcs(tree, names):
    out = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            out[node.name] = (node.lineno, node.end_lineno, node)
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in ("TRUST_DEVICE_COOKIE", "TRUST_DEVICE_DAYS"):
                    out[t.id] = (node.lineno, node.end_lineno, node)
    return out


def method_source(member, want_decorators=True):
    start, end, node = member
    text = "".join(lines[start - 1 : end])
    if not want_decorators and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        # strip leading decorator lines
        src_lines = text.splitlines(keepends=True)
        i = 0
        while i < len(src_lines) and src_lines[i].lstrip().startswith("@"):
            i += 1
        text = "".join(src_lines[i:])
    return text


def build_mixin(class_name, docstring, attr_names, method_names, members, imports):
    parts = [docstring, ""]
    parts.extend(imports)
    parts.append("")
    parts.append(f"class {class_name}:")
    body = []
    for a in attr_names:
        if a not in members:
            raise SystemExit(f"missing attr {a}")
        body.append(method_source(members[a]).rstrip("\n"))
        body.append("")
    for name in method_names:
        if name not in members:
            raise SystemExit(f"missing method {name}")
        body.append(method_source(members[name]).rstrip("\n"))
        body.append("")
    # ensure body non-empty and properly indented already (methods are indented with 4 spaces)
    text = "\n".join(body).rstrip() + "\n"
    parts.append(text)
    return "\n".join(parts) + "\n"


def main():
    tree = ast.parse(src)
    members = extract_class_members(tree)
    funcs = extract_module_funcs(tree, set(HELPERS) | set(CONST_NAMES))

    # --- auth helpers file ---
    auth_lines = [
        '"""Auth helpers: TOTP, trusted devices, pending tokens, CSRF (shared by app and mixins)."""',
        "import secrets",
        "from http import cookies",
        "",
        "from db import now",
        "",
    ]
    for name in CONST_NAMES + HELPERS:
        if name not in funcs:
            raise SystemExit(f"missing helper {name}")
        auth_lines.append(method_source(funcs[name]).rstrip("\n"))
        auth_lines.append("")
        auth_lines.append("")
    (ROOT / "auth_util.py").write_text("\n".join(auth_lines).rstrip() + "\n", encoding="utf-8")

    orders_imports = [
        "import sqlite3",
        "from urllib.parse import parse_qs",
        "",
        "from db import connect, row_to_dict, rows_to_list, now, ensure_thread",
        "from common import PAGE_SIZE, check_rate_limit, create_notification, store_upload, parse_deadline_days, csv_safe",
        "from auth_util import create_pending_token  # noqa: F401  (kept for potential email flows)",
    ]
    accounts_imports = [
        "import re",
        "import secrets",
        "import sqlite3",
        "from http import cookies",
        "from urllib.parse import urlparse, parse_qs",
        "",
        "from db import connect, row_to_dict, rows_to_list, now, create_user, hash_password, verify_password, purge_expired_sessions",
        "from common import PAGE_SIZE, check_rate_limit, create_notification, store_upload, csv_safe, is_dev_mode, public_base_url",
        "from auth_util import (",
        "    verify_totp, get_tfa_trust_secret, make_trust_cookie, read_trusted_user_id,",
        "    create_pending_token, generate_csrf_token, TRUST_DEVICE_COOKIE, TRUST_DEVICE_DAYS,",
        ")",
    ]
    market_imports = [
        "import re",
        "import sqlite3",
        "from urllib.parse import parse_qs",
        "",
        "from db import connect, row_to_dict, rows_to_list, now, UPLOAD_DIR, COMPANY_TYPES",
        "from common import PAGE_SIZE, check_rate_limit, create_notification, store_upload",
    ]

    # datetime import for api_auth_token
    auth_token_src = method_source(members["api_auth_token"])
    if "import datetime" not in auth_token_src and "datetime." in auth_token_src:
        accounts_imports.append("import datetime")

    (ROOT / "api_orders.py").write_text(
        build_mixin(
            "OrderMixin",
            '"""Order lifecycle API mixin: list/create, stages, accept, proposals, funnel, deadlines."""',
            ATTR_ORDERS,
            ORDERS,
            members,
            orders_imports,
        ),
        encoding="utf-8",
    )
    (ROOT / "api_accounts.py").write_text(
        build_mixin(
            "AccountMixin",
            '"""Account/session API mixin: auth, 2FA, profile, notifications, tariff, documents."""',
            [],
            ACCOUNTS,
            members,
            accounts_imports,
        ),
        encoding="utf-8",
    )
    (ROOT / "api_market.py").write_text(
        build_mixin(
            "MarketMixin",
            '"""Catalog/market API mixin: companies, services, reviews, search, articles, chat, reports."""',
            [],
            MARKET,
            members,
            market_imports,
        ),
        encoding="utf-8",
    )

    # --- rebuild app.py ---
    remove_ranges = []  # (start, end) 1-based inclusive
    for name in HELPERS + CONST_NAMES:
        s, e, _ = funcs[name]
        remove_ranges.append((s, e))
    for name in ATTR_ORDERS + ORDERS + ACCOUNTS + MARKET:
        s, e, _ = members[name]
        remove_ranges.append((s, e))

    # merge ranges separated only by blank lines
    remove_ranges.sort()
    keep = []
    for s, e in remove_ranges:
        if keep and s <= keep[-1][1] + 3:
            keep[-1][1] = max(keep[-1][1], e)
        else:
            keep.append([s, e])

    drop = set()
    for s, e in keep:
        for i in range(s, e + 1):
            drop.add(i)

    new_lines = [ln for i, ln in enumerate(lines, 1) if i not in drop]

    # clean excessive blank runs
    text = "".join(new_lines)
    text = re.sub(r"\n{4,}", "\n\n\n", text)

    # fix imports: remove unused from app if helpers gone, add mixins + auth_util
    # replace MeblioHandler bases
    text = text.replace(
        "class MeblioHandler(AdminMixin, CatalogMixin, AiMixin, BaseHTTPRequestHandler):",
        "class MeblioHandler(AccountMixin, OrderMixin, MarketMixin, AdminMixin, CatalogMixin, AiMixin, BaseHTTPRequestHandler):",
    )
    # add imports after existing api_* imports
    old_imp = (
        "from api_admin import AdminMixin\n"
        "from api_catalog import CatalogMixin\n"
        "from api_ai import AiMixin\n"
    )
    new_imp = (
        "from api_admin import AdminMixin\n"
        "from api_catalog import CatalogMixin\n"
        "from api_ai import AiMixin\n"
        "from api_accounts import AccountMixin\n"
        "from api_orders import OrderMixin\n"
        "from api_market import MarketMixin\n"
        "from auth_util import (\n"
        "    verify_totp,\n"
        "    TRUST_DEVICE_COOKIE,\n"
        "    TRUST_DEVICE_DAYS,\n"
        "    get_tfa_trust_secret,\n"
        "    make_trust_cookie,\n"
        "    verify_trust_cookie,\n"
        "    read_trusted_user_id,\n"
        "    create_pending_token,\n"
        "    generate_csrf_token,\n"
        "    validate_csrf_token,\n"
        ")\n"
    )
    if old_imp not in text:
        raise SystemExit("import block not found")
    text = text.replace(old_imp, new_imp, 1)

    APP.write_text(text, encoding="utf-8")
    print("wrote auth_util.py, api_orders.py, api_accounts.py, api_market.py")
    print("app.py lines:", len(text.splitlines()))
    # verify compile
    ast.parse(text)
    for f in ("auth_util.py", "api_orders.py", "api_accounts.py", "api_market.py"):
        ast.parse((ROOT / f).read_text(encoding="utf-8"))
        print("ok", f)


if __name__ == "__main__":
    main()
