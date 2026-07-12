import httpx
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

def send_whatsapp_message(account_sid: str, auth_token: str, from_number: str, to_number: str,
                          body: str, media_url: Optional[str] = None) -> Optional[str]:
    """Send a WhatsApp message via Twilio."""
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    auth = (account_sid, auth_token)
    data = {
        "From": from_number,
        "To": to_number,
        "Body": body,
    }
    if media_url:
        data["MediaUrl"] = media_url

    try:
        with httpx.Client() as client:
            resp = client.post(url, data=data, auth=auth, timeout=15)
            if resp.status_code in (200, 201):
                return resp.json().get("sid")
            logger.error("Twilio send error %s: %s", resp.status_code, resp.text)
            return None
    except Exception as e:
        logger.error("Twilio send exception: %s", e)
        return None

def set_webhook(account_sid: str, auth_token: str, number_sid: str, webhook_url: str) -> bool:
    """Update the incoming message webhook for a Twilio number."""
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/IncomingPhoneNumbers/{number_sid}.json"
    auth = (account_sid, auth_token)
    data = {"SmsUrl": webhook_url, "SmsMethod": "POST"}
    try:
        with httpx.Client() as client:
            resp = client.post(url, data=data, auth=auth, timeout=15)
            if resp.status_code in (200, 201):
                logger.info("Webhook set for number %s to %s", number_sid, webhook_url)
                return True
            logger.error("Failed to set webhook: %s", resp.text)
            return False
    except Exception as e:
        logger.error("Webhook update exception: %s", e)
        return False

def buy_number(account_sid: str, auth_token: str, phone_number: str,
               friendly_name: Optional[str] = None) -> Dict[str, Any]:
    """Purchase a Twilio number and return the result."""
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/IncomingPhoneNumbers.json"
    auth = (account_sid, auth_token)
    data = {
        "PhoneNumber": phone_number,
        "FriendlyName": friendly_name or phone_number,
    }
    try:
        with httpx.Client() as client:
            resp = client.post(url, data=data, auth=auth, timeout=30)
            if resp.status_code in (200, 201):
                return resp.json()
            raise Exception(f"Twilio purchase error {resp.status_code}: {resp.text}")
    except Exception as e:
        logger.error("Twilio purchase exception: %s", e)
        raise