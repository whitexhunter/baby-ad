import json
import asyncio
import os
from typing import Any, Dict, List

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

_locks = {}

def _get_lock(filename: str) -> asyncio.Lock:
    if filename not in _locks:
        _locks[filename] = asyncio.Lock()
    return _locks[filename]

async def read_json(filename: str) -> Any:
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        return {} if filename == "users.json" else []
    async with _get_lock(filename):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

async def write_json(filename: str, data: Any) -> None:
    path = os.path.join(DATA_DIR, filename)
    async with _get_lock(filename):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

async def get_users() -> Dict:
    return await read_json("users.json")
async def get_accounts() -> List[Dict]:
    return await read_json("accounts.json")
async def get_campaigns() -> List[Dict]:
    return await read_json("campaigns.json")
async def get_subscriptions() -> List[Dict]:
    return await read_json("subscriptions.json")
async def get_keys() -> List[Dict]:
    return await read_json("keys.json")

async def save_users(data: Dict) -> None:
    await write_json("users.json", data)
async def save_accounts(data: List[Dict]) -> None:
    await write_json("accounts.json", data)
async def save_campaigns(data: List[Dict]) -> None:
    await write_json("campaigns.json", data)
async def save_subscriptions(data: List[Dict]) -> None:
    await write_json("subscriptions.json", data)
async def save_keys(data: List[Dict]) -> None:
    await write_json("keys.json", data)

async def upsert_user(discord_id: str, updates: Dict) -> None:
    users = await get_users()
    if discord_id not in users:
        users[discord_id] = {}
    users[discord_id].update(updates)
    await save_users(users)