import logging
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("confam.telegram")

MAX_IMAGES = 6
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_MESSAGE_LENGTH = 3900
API_TIMEOUT = httpx.Timeout(120.0, connect=10.0)

BASE_URL = os.getenv(
    "CONFAM_API_BASE_URL",
    f"http://127.0.0.1:{os.getenv('PORT', '8000')}",
).rstrip("/")

FIELD_LABELS = {
    "product_name": "Product name",
    "active_ingredients": "Active ingredients",
    "strength": "Strength",
    "dosage_form": "Dosage form",
    "nafdac_reg_number": "NAFDAC registration number",
    "marketer": "Reg. Holder / Marketer",
    "batch_number": "Batch number",
    "manufacturing_date": "Manufacturing date",
    "expiry_date": "Expiry date",
}

STATUS_LABELS = {
    "no_significant_discrepancy_detected":
        "No significant discrepancy detected",
    "verification_concern_detected": "Verification concern detected",
    "high_risk_discrepancy_detected":
        "High-risk discrepancy detected",
}

SESSIONS = {}


def new_session():
    return {
        "images": [],
        "claims": None,
        "uncertain_fields": [],
        "limitations": [],
        "reviewed": False,
        "confirmed": False,
    }


def session_key(update):
    return (update.effective_chat.id, update.effective_user.id)


def session_for(update):
    key = session_key(update)
    if key not in SESSIONS:
        SESSIONS[key] = new_session()
    return SESSIONS[key]


def private_chat_only(update):
    return (
        update.effective_chat is not None
        and update.effective_chat.type == "private"
    )


async def require_private_chat(update):
    if private_chat_only(update):
        return True

    if update.effective_message:
        await update.effective_message.reply_text(
            "For privacy, please use CONFAM in a private chat with the bot."
        )
    return False


def shorten(text, limit=MAX_MESSAGE_LENGTH):
    if len(text) <= limit:
        return text
    return text[: limit - 35] + "\n\n[Message shortened]"


def claims_text(claims):
    lines = ["Review the extracted claims:\n"]
    for field, label in FIELD_LABELS.items():
        value = claims.get(field)
        lines.append(f"{label}: {value or 'Not detected'}")

    lines.extend([
        "",
        "To correct a field, use:",
        "/edit field_name corrected value",
        "Example: /edit product_name Paracetamol 500mg",
        "",
        "Inspect every field. Then tap the confirmation button below.",
    ])
    return shorten("\n".join(lines))


async def api_error_message(response):
    try:
        data = response.json()
        detail = data.get("detail", "The API returned an error.")
        if isinstance(detail, list):
            detail = "; ".join(
                str(item.get("msg", item)) if isinstance(item, dict)
                else str(item)
                for item in detail[:4]
            )
        return str(detail)[:700]
    except Exception:
        return f"The API returned HTTP {response.status_code}."


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_private_chat(update):
        return

    await update.effective_message.reply_text(
        "Welcome to CONFAM.\n\n"
        "I compare visible medicine-package claims with available "
        "regulatory records and safety-alert evidence. I cannot certify "
        "that a medicine is authentic or safe.\n\n"
        "HOW TO CHECK A PACKAGE\n"
        "1. Send 1–6 clear photos of the same package.\n"
        "2. Send /done to extract the visible claims.\n"
        "3. Use /review and inspect the extracted values.\n"
        "4. Correct mistakes with /edit field_name value.\n"
        "5. Review and confirm the claims explicitly.\n"
        "6. Send /verify to run the evidence check.\n\n"
        "Commands:\n"
        "/start — show these instructions\n"
        "/done — extract claims from uploaded photos\n"
        "/review — review the claims\n"
        "/edit — correct a claim\n"
        "/verify — check confirmed claims\n"
        "/cancel — clear your current session"
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_private_chat(update):
        return

    SESSIONS.pop(session_key(update), None)
    await update.effective_message.reply_text(
        "Your current session has been cleared. "
        "Send package photos to start a new check."
    )


async def receive_image(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    if not await require_private_chat(update):
        return

    message = update.effective_message
    session = session_for(update)

    if session["claims"] is not None:
        session = new_session()
        SESSIONS[session_key(update)] = session

    if len(session["images"]) >= MAX_IMAGES:
        await message.reply_text(
            "You have reached the six-photo limit. Send /done to continue."
        )
        return

    if message.photo:
        photo = message.photo[-1]
        file_id = photo.file_id
        file_size = photo.file_size
        mime_type = "image/jpeg"
    elif message.document:
        document = message.document
        file_id = document.file_id
        file_size = document.file_size
        mime_type = (document.mime_type or "").lower()

        if not mime_type.startswith("image/"):
            await message.reply_text(
                "Please send a photo or an image document."
            )
            return
    else:
        return

    if file_size is not None and file_size > MAX_IMAGE_BYTES:
        await message.reply_text(
            "That image exceeds CONFAM's 5 MB per-photo limit."
        )
        return

    try:
        telegram_file = await context.bot.get_file(file_id)
        image_bytes = bytes(await telegram_file.download_as_bytearray())

        if len(image_bytes) > MAX_IMAGE_BYTES:
            await message.reply_text(
                "That image exceeds CONFAM's 5 MB per-photo limit."
            )
            return
    except Exception:
        logger.exception("Telegram image download failed")
        await message.reply_text(
            "I couldn't download that image. Please try again."
        )
        return

    session["images"].append((image_bytes, mime_type))
    session["reviewed"] = False
    session["confirmed"] = False

    await message.reply_text(
        f"Photo {len(session['images'])}/{MAX_IMAGES} received. "
        "Send more views of the same package, or use /done."
    )


async def done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_private_chat(update):
        return

    message = update.effective_message
    session = session_for(update)

    if not session["images"]:
        await message.reply_text(
            "No photos received. Send 1–6 package photos first."
        )
        return

    if session["claims"] is not None:
        await message.reply_text(
            "Claims have already been extracted. Use /review to inspect them."
        )
        return

    images = list(session["images"])
    await message.reply_text(
        f"Sending {len(images)} photo(s) to CONFAM for extraction. "
        "This may take a little while. Verification will not run automatically."
    )

    files = [
        (
            "images",
            (f"package_{index}.jpg", image_bytes, mime_type),
        )
        for index, (image_bytes, mime_type) in enumerate(images, start=1)
    ]

    try:
        async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
            response = await client.post(
                f"{BASE_URL}/api/v1/extract-multiple",
                files=files,
            )

        if response.is_error:
            detail = await api_error_message(response)
            await message.reply_text(
                "CONFAM could not extract the claims.\n"
                f"API response: {detail}\n"
                "Your photos remain in this session. You can retry /done."
            )
            return

        extraction = response.json()
        claims = extraction.get("claims")
        if not isinstance(claims, dict):
            raise ValueError("The extraction response had no claims object.")

        session["claims"] = claims
        session["uncertain_fields"] = extraction.get(
            "uncertain_fields", []
        )
        session["limitations"] = extraction.get("limitations", [])
        session["images"] = []
        session["reviewed"] = False
        session["confirmed"] = False

    except httpx.RequestError:
        logger.exception("Extraction API request failed")
        await message.reply_text(
            "CONFAM's API could not be reached. Check that the backend "
            "is running and the API URL is correct. Your photos remain "
            "in this session; retry /done when the API is available."
        )
        return
    except Exception:
        logger.exception("Unexpected extraction response")
        await message.reply_text(
            "CONFAM received an unexpected extraction response. "
            "No verification was performed. Please retry or /cancel."
        )
        return

    await message.reply_text(
        "Extraction complete. No verification has been performed.\n\n"
        "Use /review to inspect the claims before proceeding."
    )


async def review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_private_chat(update):
        return

    message = update.effective_message
    session = session_for(update)

    if not session["claims"]:
        await message.reply_text(
            "No extracted claims are available. Send photos and use /done."
        )
        return

    session["reviewed"] = True
    session["confirmed"] = False

    text = claims_text(session["claims"])
    uncertain = session["uncertain_fields"]
    if uncertain:
        labels = [
            FIELD_LABELS.get(field, field) for field in uncertain
        ]
        text += "\n\nFields needing extra attention: " + ", ".join(labels)

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "I reviewed these claims",
            callback_data="confirm_claims",
        )
    ]])

    await message.reply_text(
        shorten(text), reply_markup=keyboard
    )


async def confirm_claims(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    if not private_chat_only(update):
        await query.answer("Please use the bot in a private chat.")
        return

    await query.answer()
    session = session_for(update)

    if not session["claims"] or not session["reviewed"]:
        await query.edit_message_text(
            "These claims are not ready for confirmation. Use /review again."
        )
        return

    session["confirmed"] = True
    await query.edit_message_text(
        "You confirmed that you reviewed the extracted claims.\n\n"
        "No verification has run yet. Send /verify when ready. "
        "Any edit will require a new review and confirmation."
    )


async def edit_claim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_private_chat(update):
        return

    message = update.effective_message
    session = session_for(update)

    if not session["claims"]:
        await message.reply_text(
            "No extracted claims to edit. Send photos and use /done first."
        )
        return

    if len(context.args) < 2:
        await message.reply_text(
            "Usage: /edit field_name corrected value\n"
            "Example: /edit product_name Paracetamol 500mg\n\n"
            "Field names:\n" + ", ".join(FIELD_LABELS)
        )
        return

    field = context.args[0].strip().lower()
    if field not in FIELD_LABELS:
        await message.reply_text(
            "Unknown field. Choose one of:\n" + ", ".join(FIELD_LABELS)
        )
        return

    value = " ".join(context.args[1:]).strip()
    session["claims"][field] = None if value == "-" else value

    # Every edit invalidates previous review and confirmation.
    session["reviewed"] = False
    session["confirmed"] = False

    await message.reply_text(
        f"{FIELD_LABELS[field]} updated.\n"
        "Use /review to inspect all claims again, then confirm before /verify."
    )


def format_verification(result):
    lines = [
        "CONFAM EVIDENCE CHECK",
        "",
        STATUS_LABELS.get(
            result.get("status"),
            str(result.get("status", "Unknown")).replace("_", " "),
        ),
        "",
        str(result.get("summary") or "No summary was returned."),
        "",
        f"Registry lookup: {result.get('registry_lookup_status', 'unknown')}",
        f"Safety-alert lookup: {result.get('alert_lookup_status', 'unknown')}",
    ]

    if result.get("matched_record"):
        lines.append("A registry record was selected for comparison.")
    else:
        lines.append("No matching registry record was selected for comparison.")

    checks = result.get("checks") or []
    if checks:
        lines.extend(["", "Field comparisons:"])
        for check in checks[:10]:
            if not isinstance(check, dict):
                continue
            field = check.get("field", "Field")
            label = FIELD_LABELS.get(field, field)
            outcome = str(check.get("outcome", "unknown")).replace("_", " ")
            package_value = check.get("package_value")
            reference_value = check.get("reference_value")
            explanation = check.get("explanation", "")

            lines.append(f"- {label}: {outcome}.")
            if package_value:
                lines.append(f"  Package: {package_value}")
            if reference_value:
                lines.append(f"  Reference: {reference_value}")
            explanation_text = str(explanation).casefold()
            internal_comparison_detail = any(
                term in explanation_text
                for term in (
                    "normaliz",
                    "comparison score",
                    "similarity score",
                    "text-similarity",
                    "text similarity",
                )
            )
            if explanation and not internal_comparison_detail:
                lines.append(f"  Evidence: {explanation}")

    alerts = result.get("safety_alerts") or []
    if alerts:
        lines.extend(["", "Relevant safety-alert evidence:"])
        for alert in alerts[:3]:
            if not isinstance(alert, dict):
                continue

            lines.append(
                f"- {alert.get('title', 'Safety alert')} "
                f"(product: {alert.get('product_name', 'not specified')})"
            )

            if alert.get("matched_batch_number"):
                lines.append(
                    f"  Matching batch: {alert['matched_batch_number']}"
                )
            if alert.get("batch_match_status"):
                lines.append(
                    f"  Batch comparison: "
                    f"{str(alert['batch_match_status']).replace('_', ' ')}"
                )
            if alert.get("explanation"):
                lines.append(f"  Evidence: {alert['explanation']}")
            if alert.get("source_name"):
                lines.append(f"  Source: {alert['source_name']}")
            if alert.get("source_url"):
                lines.append(f"  Source link: {alert['source_url']}")

    if result.get("source_name"):
        lines.extend(["", f"Registry source: {result['source_name']}"])
    if result.get("source_url"):
        lines.append(f"Registry source link: {result['source_url']}")

    if result.get("registry_retrieved_at"):
        lines.append(
            f"Registry evidence retrieved: {result['registry_retrieved_at']}"
        )
    elif result.get("registry_snapshot_retrieved_at"):
        lines.append(
            "Registry snapshot retrieved: "
            f"{result['registry_snapshot_retrieved_at']}"
        )

    if result.get("alerts_snapshot_retrieved_at"):
        lines.append(
            "Safety-alert snapshot retrieved: "
            f"{result['alerts_snapshot_retrieved_at']}"
        )

    if result.get("briefing"):
        lines.extend(["", "Evidence briefing:", str(result["briefing"])])

    if result.get("semantic_reconciliation"):
        lines.extend([
            "",
            "Additional reconciliation:",
            str(result["semantic_reconciliation"]),
        ])

    limitations = result.get("limitations") or []
    if limitations:
        lines.extend(["", "Important limitations:"])
        for limitation in limitations[:5]:
            lines.append(f"- {limitation}")

    lines.extend([
        "",
        "IMPORTANT: This result reports available evidence and discrepancies. "
        "It does not certify that a medicine is authentic or safe. "
        "No matching safety alert is not proof of safety. Consult a "
        "pharmacist or the relevant regulator if you have concerns.",
    ])

    return shorten("\n".join(lines))

async def verify_claims(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    if not await require_private_chat(update):
        return

    message = update.effective_message
    session = session_for(update)

    if not session["claims"]:
        await message.reply_text(
            "No claims are ready. Send photos and use /done first."
        )
        return

    if not session["reviewed"] or not session["confirmed"]:
        await message.reply_text(
            "Verification is locked. Use /review, inspect the claims, "
            "and tap 'I reviewed these claims' before sending /verify."
        )
        return

    claims = dict(session["claims"])
    session["confirmed"] = False

    await message.reply_text(
        "Checking the confirmed claims against available registry "
        "and safety-alert evidence..."
    )

    try:
        async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
            response = await client.post(
                f"{BASE_URL}/api/v1/verify",
                json={"claims": claims},
            )

        if response.is_error:
            detail = await api_error_message(response)
            session["reviewed"] = False
            await message.reply_text(
                "The verification API returned an error. No conclusion "
                f"can be drawn from this attempt.\nAPI response: {detail}\n"
                "Use /review and confirm again before retrying."
            )
            return

        result = response.json()
        await message.reply_text(format_verification(result))

    except httpx.RequestError:
        logger.exception("Verification API request failed")
        session["reviewed"] = False
        await message.reply_text(
            "CONFAM's API could not be reached. No conclusion can be drawn. "
            "Check that the backend is running, then use /review and "
            "confirm again before retrying."
        )
    except Exception:
        logger.exception("Unexpected verification response")
        session["reviewed"] = False
        await message.reply_text(
            "The verification response could not be processed. "
            "No conclusion can be drawn. Use /review and confirm again "
            "before retrying."
        )



def build_application(token: str) -> Application:
    """Build the bot and register its existing handlers."""
    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("done", done))
    application.add_handler(CommandHandler("review", review))
    application.add_handler(CommandHandler("edit", edit_claim))
    application.add_handler(CommandHandler("verify", verify_claims))
    application.add_handler(
        CallbackQueryHandler(
            confirm_claims, pattern="^confirm_claims$"
        )
    )
    application.add_handler(
        MessageHandler(
            filters.PHOTO | filters.Document.IMAGE,
            receive_image,
        )
    )
    return application


def main():
    """Run the bot in polling mode for local development."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing from backend/.env."
        )

    application = build_application(token)
    logger.info("Starting CONFAM Telegram bot; API base URL: %s", BASE_URL)
    application.run_polling()


if __name__ == "__main__":
    main()
