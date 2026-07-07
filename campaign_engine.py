import asyncio
import datetime
import logging
from typing import Dict, List
import traceback

from storage import get_campaigns, save_campaigns, get_accounts, get_users, save_users, get_subscriptions, save_subscriptions
from discord_api import send_message, send_message_with_image, get_dm_channels, get_last_message
from crypto_utils import decrypt_token

logger = logging.getLogger(__name__)

_running = True
_dm_tasks: Dict[str, asyncio.Task] = {}
# Track last send time per channel campaign
_campaign_last_sent: Dict[str, float] = {}

class CampaignEngine:
    def __init__(self):
        self.loop = asyncio.get_event_loop()
        self._channel_task = None
        self._expiry_task = None
        self._dm_monitor_task = None

    def start(self):
        self._channel_task = self.loop.create_task(self._channel_worker())
        self._expiry_task = self.loop.create_task(self._expiry_checker())
        self._dm_monitor_task = self.loop.create_task(self._dm_monitor())

    async def stop(self):
        global _running
        _running = False
        for task in [self._channel_task, self._expiry_task, self._dm_monitor_task]:
            if task:
                task.cancel()
        for task in _dm_tasks.values():
            task.cancel()
        await asyncio.sleep(2)

    # ---------- Channel Worker (with per‑campaign delay) ----------
    async def _channel_worker(self):
        global _running
        while _running:
            try:
                all_campaigns = await get_campaigns()
                running_campaigns = [c for c in all_campaigns if c.get("type") == "channel" and c.get("status") == "running"]
                now = datetime.datetime.now(datetime.UTC).timestamp()
                for campaign in running_campaigns:
                    campaign_id = campaign["id"]
                    delay = campaign.get("delay", 1)  # seconds
                    last_sent = _campaign_last_sent.get(campaign_id, 0)
                    if now - last_sent >= delay:
                        await self._process_channel_campaign(campaign)
                        _campaign_last_sent[campaign_id] = now
                # If no campaigns, sleep a bit
                if not running_campaigns:
                    await asyncio.sleep(2)
            except Exception as e:
                logger.error(f"Channel worker error: {e}\n{traceback.format_exc()}")
            await asyncio.sleep(2)  # cycle interval

    async def _process_channel_campaign(self, campaign: Dict):
        campaign_id = campaign["id"]
        discord_id = campaign["discord_id"]
        account_id = campaign["account_id"]
        channels = campaign.get("channels", [])
        messages = campaign.get("messages", [])
        sent = campaign.get("messages_sent", 0)
        failed = campaign.get("messages_failed", 0)
        total_expected = len(channels) * len(messages)

        if sent >= total_expected:
            campaign["status"] = "completed"
            campaign["completed_at"] = datetime.datetime.now(datetime.UTC).isoformat()
            await save_campaigns(await get_campaigns())
            return

        msg_index = sent // len(channels)
        ch_index = sent % len(channels)
        channel_id = channels[ch_index]
        msg_data = messages[msg_index]
        content = msg_data["content"]
        image_url = msg_data.get("image_url")

        accounts = await get_accounts()
        account = next((a for a in accounts if a["id"] == account_id), None)
        if not account or not account.get("valid"):
            campaign["status"] = "failed"
            campaign["failed_reason"] = "Account invalid"
            await save_campaigns(await get_campaigns())
            return

        try:
            token = decrypt_token(account["encrypted_token"])
        except Exception as e:
            logger.error(f"Token decryption error for campaign {campaign_id}: {e}")
            campaign["status"] = "failed"
            campaign["failed_reason"] = "Token decryption error"
            await save_campaigns(await get_campaigns())
            return

        success = False
        try:
            if image_url:
                success = await send_message_with_image(token, channel_id, content, image_url)
            else:
                success = await send_message(token, channel_id, content)
        except Exception as e:
            logger.error(f"Send error for campaign {campaign_id}, channel {channel_id}: {e}")
            success = False

        if not success:
            failed += 1
            logger.warning(f"Failed to send message {sent+1}/{total_expected} in campaign {campaign_id} to channel {channel_id}")
        else:
            logger.info(f"Sent message {sent+1}/{total_expected} in campaign {campaign_id} to channel {channel_id}")

        sent += 1
        campaign["messages_sent"] = sent
        campaign["messages_failed"] = failed
        if sent >= total_expected:
            campaign["status"] = "completed"
            campaign["completed_at"] = datetime.datetime.now(datetime.UTC).isoformat()
        await save_campaigns(await get_campaigns())

    # ---------- DM Monitor (unchanged) ----------
    async def _dm_monitor(self):
        global _running
        while _running:
            try:
                campaigns = await get_campaigns()
                dm_campaigns = [c for c in campaigns if c.get("type") == "dm_auto_reply" and c.get("status") == "running"]
                active_users = set(c["discord_id"] for c in dm_campaigns)
                for uid in active_users:
                    if uid not in _dm_tasks or _dm_tasks[uid].done():
                        task = asyncio.create_task(self._dm_responder(uid))
                        _dm_tasks[uid] = task
                for uid in list(_dm_tasks.keys()):
                    if uid not in active_users:
                        if _dm_tasks[uid] and not _dm_tasks[uid].done():
                            _dm_tasks[uid].cancel()
                        del _dm_tasks[uid]
            except Exception as e:
                logger.error(f"DM monitor error: {e}\n{traceback.format_exc()}")
            await asyncio.sleep(10)

    async def _dm_responder(self, discord_id: str):
        global _running
        while _running:
            try:
                campaigns = await get_campaigns()
                user_campaigns = [c for c in campaigns if c["discord_id"] == discord_id and c["type"] == "dm_auto_reply" and c["status"] == "running"]
                if not user_campaigns:
                    break

                account_id = user_campaigns[0]["account_id"]
                accounts = await get_accounts()
                account = next((a for a in accounts if a["id"] == account_id), None)
                if not account or not account.get("valid"):
                    for c in user_campaigns:
                        c["status"] = "failed"
                        c["failed_reason"] = "Account invalid"
                    await save_campaigns(await get_campaigns())
                    break

                try:
                    token = decrypt_token(account["encrypted_token"])
                except Exception:
                    for c in user_campaigns:
                        c["status"] = "failed"
                        c["failed_reason"] = "Token decryption error"
                    await save_campaigns(await get_campaigns())
                    break

                channels = await get_dm_channels(token)
                for ch in channels:
                    last_msg = await get_last_message(token, ch["id"])
                    if not last_msg:
                        continue
                    if last_msg["author"]["id"] == account["discord_user_id"]:
                        continue

                    for camp in user_campaigns:
                        if last_msg["id"] == camp.get("last_replied_id"):
                            continue
                        keywords = camp.get("keywords", [])
                        if keywords:
                            msg_content = last_msg.get("content", "")
                            if not any(k.lower() in msg_content.lower() for k in keywords):
                                continue
                        reply_messages = camp.get("messages", [])
                        for reply in reply_messages:
                            await send_message(token, ch["id"], reply)
                        camp["replied_count"] = camp.get("replied_count", 0) + len(reply_messages)
                        camp["last_replied_id"] = last_msg["id"]
                        await save_campaigns(await get_campaigns())
                        break
            except Exception as e:
                logger.error(f"DM responder for {discord_id} error: {e}\n{traceback.format_exc()}")
            await asyncio.sleep(5)

    # ---------- Expiry Checker (unchanged) ----------
    async def _expiry_checker(self):
        global _running
        while _running:
            try:
                now = datetime.datetime.now(datetime.UTC)
                subs = await get_subscriptions()
                changed = False
                for s in subs:
                    if s["status"] == "confirmed":
                        expires_at = datetime.datetime.fromisoformat(s["expires_at"])
                        if expires_at < now:
                            s["status"] = "expired"
                            changed = True
                if changed:
                    await save_subscriptions(subs)

                users = await get_users()
                changed_trials = False
                for uid, data in users.items():
                    if data.get("trial_active") and data.get("trial_expires_at"):
                        trial_exp = datetime.datetime.fromisoformat(data["trial_expires_at"])
                        if trial_exp < now:
                            data["trial_active"] = False
                            data["trial_used"] = True
                            changed_trials = True
                if changed_trials:
                    await save_users(users)
            except Exception as e:
                logger.error(f"Expiry checker error: {e}\n{traceback.format_exc()}")
            await asyncio.sleep(300)

engine = CampaignEngine()

def start_engine():
    engine.start()

async def shutdown_engine():
    await engine.stop()

async def start_dm_responder(discord_id: str):
    if discord_id in _dm_tasks and not _dm_tasks[discord_id].done():
        return
    task = asyncio.create_task(engine._dm_responder(discord_id))
    _dm_tasks[discord_id] = task

async def resume_running_campaigns():
    campaigns = await get_campaigns()
    dm_users = set(c["discord_id"] for c in campaigns if c["type"] == "dm_auto_reply" and c["status"] == "running")
    for uid in dm_users:
        await start_dm_responder(uid)
    # Channel campaigns: last_sent times are reset on startup (will send as soon as delay allows)