import aiohttp
from typing import Optional, List, Dict

BASE_URL = "https://discord.com/api/v10"
HEADERS = {"User-Agent": "DiscordBot (HUNTER, 1.0.0)"}

async def _request(method: str, endpoint: str, token: str, **kwargs) -> Optional[Dict]:
    url = f"{BASE_URL}{endpoint}"
    headers = {**HEADERS, "Authorization": token}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.request(method, url, headers=headers, **kwargs) as resp:
                if resp.status in (200, 201, 204):
                    if resp.status == 204:
                        return {"success": True}
                    return await resp.json()
                return None
        except Exception:
            return None

async def validate_token(token: str) -> Optional[Dict]:
    return await _request("GET", "/users/@me", token)

async def send_message(token: str, channel_id: str, content: str) -> bool:
    result = await _request("POST", f"/channels/{channel_id}/messages", token, json={"content": content})
    return result is not None

async def send_message_with_image(token: str, channel_id: str, content: str, image_url: str) -> bool:
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(image_url) as img_resp:
                if img_resp.status != 200:
                    return False
                img_data = await img_resp.read()
            data = aiohttp.FormData()
            data.add_field("content", content)
            data.add_field("file", img_data, filename="image.png", content_type=img_resp.headers.get("Content-Type", "image/png"))
            async with session.post(
                f"{BASE_URL}/channels/{channel_id}/messages",
                headers={**HEADERS, "Authorization": token},
                data=data
            ) as resp:
                return resp.status == 200
        except Exception:
            return False

async def get_dm_channels(token: str) -> List[Dict]:
    result = await _request("GET", "/users/@me/channels", token)
    return result if isinstance(result, list) else []

async def get_last_message(token: str, channel_id: str) -> Optional[Dict]:
    result = await _request("GET", f"/channels/{channel_id}/messages?limit=1", token)
    if isinstance(result, list) and result:
        return result[0]
    return None