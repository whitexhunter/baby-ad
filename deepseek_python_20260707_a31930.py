import discord
import uuid
import datetime
import re
import asyncio

from storage import (
    get_users, upsert_user, get_accounts, save_accounts,
    get_campaigns, save_campaigns, get_subscriptions, save_subscriptions,
    get_keys, save_keys
)
from discord_api import validate_token
from crypto_utils import encrypt_token
from campaign_engine import start_dm_responder

# Helper
async def get_effective_plan(discord_id: str):
    subs = await get_subscriptions()
    lifetime = next((s for s in subs if s["discord_id"] == discord_id and s["plan"] == "lifetime" and s["status"] == "confirmed"), None)
    if lifetime:
        return "lifetime", {"accounts": 5, "image": True, "dm": True}
    active = next((s for s in subs if s["discord_id"] == discord_id and s["status"] == "confirmed"), None)
    if active:
        plan = active["plan"]
        limits = {
            "v1": {"accounts": 1, "image": False, "dm": False},
            "v2": {"accounts": 3, "image": True, "dm": False},
            "v3": {"accounts": 5, "image": True, "dm": True},
        }.get(plan, {"accounts": 0, "image": False, "dm": False})
        return plan, limits
    users = await get_users()
    user_data = users.get(discord_id, {})
    if user_data.get("trial_active") and user_data.get("trial_expires_at"):
        trial_exp = datetime.datetime.fromisoformat(user_data["trial_expires_at"])
        if trial_exp > datetime.datetime.now(datetime.UTC):
            return "trial", {"accounts": 5, "image": True, "dm": True}
    return "free", {"accounts": 0, "image": False, "dm": False}

# ---------- Modals ----------
class RedeemModal(discord.ui.Modal):
    def __init__(self, *args, **kwargs):
        super().__init__(title="Redeem License Key", *args, **kwargs)
        self.add_item(discord.ui.InputText(label="License Key", placeholder="HUNTER-XXXX-XXXX-XXXX", style=discord.InputTextStyle.short))

    async def callback(self, interaction: discord.Interaction):
        key_str = self.children[0].value.strip().upper()
        if not re.match(r"^HUNTER-[A-F0-9]{4}-[A-F0-9]{4}-[A-F0-9]{4}$", key_str):
            await interaction.response.send_message("Invalid key format. Use HUNTER-XXXX-XXXX-XXXX.", ephemeral=True)
            return
        keys = await get_keys()
        key_obj = next((k for k in keys if k["key"] == key_str), None)
        if not key_obj:
            await interaction.response.send_message("Key not found.", ephemeral=True)
            return
        if key_obj.get("redeemed_by"):
            await interaction.response.send_message("This key has already been used.", ephemeral=True)
            return
        discord_id = str(interaction.user.id)
        plan = key_obj["plan"]
        if plan == "lifetime":
            expires_at = datetime.datetime(2099, 12, 31, 23, 59, 59, tzinfo=datetime.UTC)
        else:
            expires_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=30)
        subs = await get_subscriptions()
        sub_id = str(uuid.uuid4())
        sub = {
            "id": sub_id,
            "discord_id": discord_id,
            "plan": plan,
            "amount": 0,
            "status": "confirmed",
            "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "expires_at": expires_at.isoformat()
        }
        subs.append(sub)
        await save_subscriptions(subs)
        key_obj["redeemed_by"] = discord_id
        key_obj["redeemed_at"] = datetime.datetime.now(datetime.UTC).isoformat()
        await save_keys(keys)
        for s in subs:
            if s["discord_id"] == discord_id and s["id"] != sub_id and s["status"] == "confirmed":
                s["status"] = "expired"
        await save_subscriptions(subs)
        await interaction.response.send_message(f"✅ Key redeemed successfully! You now have {plan.upper()} plan.", ephemeral=True)
        await interaction.message.edit(view=PanelView(interaction.user.id))

class AddAccountModal(discord.ui.Modal):
    def __init__(self, *args, **kwargs):
        super().__init__(title="Add Account", *args, **kwargs)
        self.add_item(discord.ui.InputText(label="Discord Token", placeholder="Your token here", style=discord.InputTextStyle.long))

    async def callback(self, interaction: discord.Interaction):
        token = self.children[0].value.strip()
        user_info = await validate_token(token)
        if not user_info:
            await interaction.response.send_message("Invalid token. Please check and try again.", ephemeral=True)
            return
        discord_id = str(interaction.user.id)
        plan, limits = await get_effective_plan(discord_id)
        max_accounts = limits["accounts"]
        accounts = await get_accounts()
        user_accounts = [a for a in accounts if a["discord_id"] == discord_id]
        if len(user_accounts) >= max_accounts:
            await interaction.response.send_message(f"You have reached the account limit for your plan ({max_accounts}).", ephemeral=True)
            return
        if any(a["discord_user_id"] == user_info["id"] for a in accounts):
            await interaction.response.send_message("This Discord account is already added.", ephemeral=True)
            return
        encrypted = encrypt_token(token)
        account = {
            "id": str(uuid.uuid4()),
            "discord_id": discord_id,
            "discord_user_id": user_info["id"],
            "username": user_info.get("username", "unknown"),
            "email": user_info.get("email", ""),
            "encrypted_token": encrypted,
            "valid": True,
            "added_at": datetime.datetime.now(datetime.UTC).isoformat()
        }
        accounts.append(account)
        await save_accounts(accounts)
        await interaction.response.send_message("✅ Account added successfully!", ephemeral=True)
        await interaction.message.edit(view=PanelView(interaction.user.id))

class NewCampaignModal(discord.ui.Modal):
    def __init__(self, campaign_type: str, account_id: str, *args, **kwargs):
        super().__init__(title=f"New {campaign_type} Campaign", *args, **kwargs)
        self.campaign_type = campaign_type
        self.account_id = account_id
        if campaign_type == "Channel Messaging":
            self.add_item(discord.ui.InputText(label="Campaign Name", placeholder="My Campaign", style=discord.InputTextStyle.short))
            self.add_item(discord.ui.InputText(label="Channel IDs (comma separated)", placeholder="123456789,987654321", style=discord.InputTextStyle.long))
            self.add_item(discord.ui.InputText(label="Messages (separate with ||)", placeholder="Hello || World! || This is a test", style=discord.InputTextStyle.long))
            self.add_item(discord.ui.InputText(label="Image URLs (optional, || separated)", placeholder="https://...||https://...", required=False, style=discord.InputTextStyle.long))
            self.add_item(discord.ui.InputText(label="Delay (seconds)", placeholder="1", value="1", style=discord.InputTextStyle.short))
        else:
            self.add_item(discord.ui.InputText(label="Campaign Name", placeholder="My DM Auto-Reply", style=discord.InputTextStyle.short))
            self.add_item(discord.ui.InputText(label="Reply Messages (one per line)", placeholder="Hello!\nHow can I help?", style=discord.InputTextStyle.long))
            self.add_item(discord.ui.InputText(label="Keywords (comma separated, optional)", placeholder="help,info", required=False, style=discord.InputTextStyle.long))

    async def callback(self, interaction: discord.Interaction):
        discord_id = str(interaction.user.id)
        if self.campaign_type == "Channel Messaging":
            name = self.children[0].value.strip()
            channels_str = self.children[1].value.strip()
            channels = [ch.strip() for ch in channels_str.split(",") if ch.strip()]
            if not channels:
                await interaction.response.send_message("Please provide at least one channel ID.", ephemeral=True)
                return
            messages_str = self.children[2].value.strip()
            messages_list = [m.strip() for m in messages_str.split("||") if m.strip()]
            if not messages_list:
                await interaction.response.send_message("Please provide at least one message.", ephemeral=True)
                return
            image_urls_str = self.children[3].value.strip() if len(self.children) > 3 else ""
            image_urls = [url.strip() for url in image_urls_str.split("||") if url.strip()] if image_urls_str else []
            delay_str = self.children[4].value.strip()
            try:
                delay = int(delay_str)
                if delay < 1:
                    raise ValueError
            except:
                await interaction.response.send_message("Delay must be a positive integer.", ephemeral=True)
                return

            plan, limits = await get_effective_plan(discord_id)
            if image_urls and not limits["image"]:
                await interaction.response.send_message("Image attachments require V2+ or Lifetime plan.", ephemeral=True)
                return

            messages = []
            for i, msg in enumerate(messages_list):
                entry = {"content": msg}
                if image_urls and i < len(image_urls):
                    entry["image_url"] = image_urls[i]
                messages.append(entry)

            campaign = {
                "id": str(uuid.uuid4()),
                "discord_id": discord_id,
                "account_id": self.account_id,
                "name": name,
                "type": "channel",
                "channels": channels,
                "messages": messages,
                "delay": delay,
                "status": "idle",
                "messages_sent": 0,
                "messages_failed": 0,
                "created_at": datetime.datetime.now(datetime.UTC).isoformat()
            }
            campaigns = await get_campaigns()
            campaigns.append(campaign)
            await save_campaigns(campaigns)
            campaign["status"] = "running"
            await save_campaigns(campaigns)
            await interaction.response.send_message("✅ Campaign created and started!", ephemeral=True)
            await interaction.message.edit(view=PanelView(interaction.user.id))

        else:  # DM Auto-Reply
            plan, limits = await get_effective_plan(discord_id)
            if not limits["dm"]:
                await interaction.response.send_message("DM Auto-Reply requires V3+ or Lifetime plan.", ephemeral=True)
                return
            name = self.children[0].value.strip()
            replies_str = self.children[1].value.strip()
            replies = [r.strip() for r in replies_str.split("\n") if r.strip()]
            if not replies:
                await interaction.response.send_message("Please provide at least one reply message.", ephemeral=True)
                return
            keywords_str = self.children[2].value.strip() if len(self.children) > 2 else ""
            keywords = [k.strip().lower() for k in keywords_str.split(",") if k.strip()] if keywords_str else []
            campaign = {
                "id": str(uuid.uuid4()),
                "discord_id": discord_id,
                "account_id": self.account_id,
                "name": name,
                "type": "dm_auto_reply",
                "messages": replies,
                "keywords": keywords,
                "status": "running",
                "replied_count": 0,
                "last_replied_id": None,
                "created_at": datetime.datetime.now(datetime.UTC).isoformat()
            }
            campaigns = await get_campaigns()
            campaigns.append(campaign)
            await save_campaigns(campaigns)
            await start_dm_responder(discord_id)
            await interaction.response.send_message("✅ DM Auto-Reply campaign started!", ephemeral=True)
            await interaction.message.edit(view=PanelView(interaction.user.id))

# ---------- Panel View ----------
class PanelView(discord.ui.View):
    def __init__(self, discord_id: str):
        super().__init__(timeout=None)
        self.discord_id = discord_id
        # Buttons are added by add_full_buttons after creation

    async def get_embed(self):
        discord_id = self.discord_id
        plan, limits = await get_effective_plan(discord_id)
        users = await get_users()
        user_data = users.get(discord_id, {})
        accounts = await get_accounts()
        user_accounts = [a for a in accounts if a["discord_id"] == discord_id]
        campaigns = await get_campaigns()
        user_campaigns = [c for c in campaigns if c["discord_id"] == discord_id]
        subs = await get_subscriptions()
        active_sub = next((s for s in subs if s["discord_id"] == discord_id and s["status"] == "confirmed"), None)

        embed = discord.Embed(title="Hunter's Auto ADV - Dashboard", color=0x00ff00)
        embed.add_field(name="Current Plan", value=plan.upper(), inline=True)
        if active_sub and active_sub["plan"] != "lifetime":
            expiry = datetime.datetime.fromisoformat(active_sub["expires_at"])
            embed.add_field(name="Expires", value=f"<t:{int(expiry.timestamp())}:R>", inline=True)
        elif plan == "lifetime":
            embed.add_field(name="Expires", value="Never", inline=True)
        elif user_data.get("trial_active"):
            trial_exp = datetime.datetime.fromisoformat(user_data["trial_expires_at"])
            embed.add_field(name="Trial Expires", value=f"<t:{int(trial_exp.timestamp())}:R>", inline=True)

        total_accounts = len(user_accounts)
        max_accounts = limits["accounts"]
        embed.add_field(name="Accounts", value=f"{total_accounts}/{max_accounts}", inline=True)
        embed.add_field(name="Campaigns", value=len(user_campaigns), inline=True)
        running = sum(1 for c in user_campaigns if c["status"] == "running")
        paused = sum(1 for c in user_campaigns if c["status"] == "paused")
        completed = sum(1 for c in user_campaigns if c["status"] == "completed")
        failed = sum(1 for c in user_campaigns if c["status"] == "failed")
        embed.add_field(name="Running/Paused/Completed/Failed", value=f"{running}/{paused}/{completed}/{failed}", inline=False)
        total_sent = sum(c.get("messages_sent", 0) for c in user_campaigns if c["type"] == "channel")
        total_failed = sum(c.get("messages_failed", 0) for c in user_campaigns if c["type"] == "channel")
        embed.add_field(name="Total Sent / Failed", value=f"{total_sent} / {total_failed}", inline=True)
        return embed

    @discord.ui.button(label="Redeem Key", style=discord.ButtonStyle.primary, row=0)
    async def redeem_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.send_modal(RedeemModal())

    @discord.ui.button(label="Free Trial (10 min V3)", style=discord.ButtonStyle.secondary, row=0)
    async def trial_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        discord_id = str(interaction.user.id)
        users = await get_users()
        user_data = users.get(discord_id, {})
        if user_data.get("trial_used"):
            await interaction.response.send_message("You have already used your free trial.", ephemeral=True)
            return
        if user_data.get("trial_active"):
            await interaction.response.send_message("You already have an active trial.", ephemeral=True)
            return
        expiry = datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=10)
        await upsert_user(discord_id, {"trial_active": True, "trial_expires_at": expiry.isoformat(), "trial_used": False})
        await interaction.response.send_message("✅ Trial activated for 10 minutes! Enjoy V3 features.", ephemeral=True)
        await interaction.message.edit(view=PanelView(discord_id))

    @discord.ui.button(label="Plans & Buy", style=discord.ButtonStyle.blurple, row=0)
    async def plans_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        embed = discord.Embed(title="Plans & Pricing", color=0x9b59b6)
        embed.add_field(name="Free", value="0 accounts, basic features", inline=False)
        embed.add_field(name="V1 ($3/month)", value="1 account", inline=False)
        embed.add_field(name="V2 ($5/month)", value="3 accounts, image attachments", inline=False)
        embed.add_field(name="V3 ($7/month)", value="5 accounts, image attachments, DM auto-reply", inline=False)
        embed.add_field(name="Lifetime ($30 one-time)", value="5 accounts, all features, never expires", inline=False)
        embed.add_field(name="How to buy", value="Contact an admin to purchase a license key.", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.gray, row=0)
    async def refresh_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        embed = await self.get_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    async def add_full_buttons(self):
        plan, _ = await get_effective_plan(self.discord_id)
        users = await get_users()
        user_data = users.get(self.discord_id, {})
        if plan == "free" and not user_data.get("trial_active"):
            return
        self.add_item(MyAccountsButton(self.discord_id))
        self.add_item(MyCampaignsButton(self.discord_id))
        self.add_item(AddAccountButton(self.discord_id))
        self.add_item(NewCampaignButton(self.discord_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return str(interaction.user.id) == self.discord_id

# ---------- Sub‑views ----------
class MyAccountsButton(discord.ui.Button):
    def __init__(self, discord_id: str):
        super().__init__(label="My Accounts", style=discord.ButtonStyle.blurple, row=1)
        self.discord_id = discord_id

    async def callback(self, interaction: discord.Interaction):
        accounts = await get_accounts()
        user_accounts = [a for a in accounts if a["discord_id"] == self.discord_id]
        if not user_accounts:
            await interaction.response.send_message("You have no accounts added.", ephemeral=True)
            return
        embed = discord.Embed(title="My Accounts", color=0x00ff00)
        for acc in user_accounts:
            status = "✅ Online" if acc.get("valid") else "❌ Invalid"
            embed.add_field(
                name=acc["username"],
                value=f"ID: `{acc['discord_user_id']}`\nStatus: {status}",
                inline=False
            )
        view = discord.ui.View()
        view.add_item(AccountDeleteSelect(user_accounts, self.discord_id))
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class AccountDeleteSelect(discord.ui.Select):
    def __init__(self, accounts: list, discord_id: str):
        options = []
        for acc in accounts:
            label = f"{acc['username']} ({acc['discord_user_id']})"
            options.append(discord.SelectOption(label=label[:100], value=acc["id"]))
        super().__init__(placeholder="Select account to delete", min_values=1, max_values=1, options=options)
        self.discord_id = discord_id

    async def callback(self, interaction: discord.Interaction):
        account_id = self.values[0]
        accounts = await get_accounts()
        accounts = [a for a in accounts if a["id"] != account_id]
        await save_accounts(accounts)
        await interaction.response.send_message("✅ Account deleted.", ephemeral=True)
        await interaction.message.edit(view=PanelView(self.discord_id))

class MyCampaignsButton(discord.ui.Button):
    def __init__(self, discord_id: str):
        super().__init__(label="My Campaigns", style=discord.ButtonStyle.blurple, row=1)
        self.discord_id = discord_id

    async def callback(self, interaction: discord.Interaction):
        campaigns = await get_campaigns()
        user_campaigns = [c for c in campaigns if c["discord_id"] == self.discord_id]
        if not user_campaigns:
            await interaction.response.send_message("You have no campaigns.", ephemeral=True)
            return
        embed = discord.Embed(title="My Campaigns", color=0x00ff00)
        for c in user_campaigns:
            status_emoji = {
                "idle": "⏸️",
                "running": "▶️",
                "paused": "⏸️",
                "completed": "✅",
                "failed": "❌"
            }.get(c["status"], "❓")
            sent = c.get("messages_sent", 0) if c["type"] == "channel" else c.get("replied_count", 0)
            failed = c.get("messages_failed", 0) if c["type"] == "channel" else 0
            embed.add_field(
                name=f"{c['name']} {status_emoji}",
                value=f"Type: {c['type']}\nSent/Replied: {sent}, Failed: {failed}\nStatus: {c['status']}",
                inline=False
            )
        view = discord.ui.View()
        paused_failed = [c for c in user_campaigns if c["status"] in ("paused", "failed")]
        if paused_failed:
            view.add_item(ResumeCampaignSelect(paused_failed, self.discord_id))
        running_campaigns = [c for c in user_campaigns if c["status"] == "running"]
        if running_campaigns:
            view.add_item(PauseAllButton(self.discord_id))
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class ResumeCampaignSelect(discord.ui.Select):
    def __init__(self, campaigns: list, discord_id: str):
        options = []
        for c in campaigns:
            label = f"{c['name']} ({c['status']})"
            options.append(discord.SelectOption(label=label[:100], value=c["id"]))
        super().__init__(placeholder="Select campaign to resume", min_values=1, max_values=1, options=options)
        self.discord_id = discord_id

    async def callback(self, interaction: discord.Interaction):
        campaign_id = self.values[0]
        campaigns = await get_campaigns()
        for c in campaigns:
            if c["id"] == campaign_id:
                c["status"] = "running"
                break
        await save_campaigns(campaigns)
        c = next((c for c in campaigns if c["id"] == campaign_id), None)
        if c and c["type"] == "dm_auto_reply":
            await start_dm_responder(self.discord_id)
        await interaction.response.send_message("✅ Campaign resumed.", ephemeral=True)
        await interaction.message.edit(view=PanelView(self.discord_id))

class PauseAllButton(discord.ui.Button):
    def __init__(self, discord_id: str):
        super().__init__(label="Pause All Running", style=discord.ButtonStyle.red, row=1)
        self.discord_id = discord_id

    async def callback(self, interaction: discord.Interaction):
        campaigns = await get_campaigns()
        for c in campaigns:
            if c["discord_id"] == self.discord_id and c["status"] == "running":
                c["status"] = "paused"
        await save_campaigns(campaigns)
        await interaction.response.send_message("✅ All running campaigns paused.", ephemeral=True)
        await interaction.message.edit(view=PanelView(self.discord_id))

class AddAccountButton(discord.ui.Button):
    def __init__(self, discord_id: str):
        super().__init__(label="Add Account", style=discord.ButtonStyle.success, row=2)
        self.discord_id = discord_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AddAccountModal())

class NewCampaignButton(discord.ui.Button):
    def __init__(self, discord_id: str):
        super().__init__(label="New Campaign", style=discord.ButtonStyle.success, row=2)
        self.discord_id = discord_id

    async def callback(self, interaction: discord.Interaction):
        accounts = await get_accounts()
        user_accounts = [a for a in accounts if a["discord_id"] == self.discord_id and a.get("valid")]
        if not user_accounts:
            await interaction.response.send_message("You need at least one valid account to create a campaign.", ephemeral=True)
            return
        view = discord.ui.View()
        view.add_item(CampaignTypeSelect(user_accounts, self.discord_id))
        await interaction.response.send_message("Select campaign type:", view=view, ephemeral=True)

class CampaignTypeSelect(discord.ui.Select):
    def __init__(self, accounts: list, discord_id: str):
        options = [
            discord.SelectOption(label="Channel Messaging", value="channel"),
            discord.SelectOption(label="DM Auto-Reply", value="dm_auto_reply"),
        ]
        super().__init__(placeholder="Choose campaign type", min_values=1, max_values=1, options=options)
        self.accounts = accounts
        self.discord_id = discord_id

    async def callback(self, interaction: discord.Interaction):
        campaign_type = self.values[0]
        view = discord.ui.View()
        view.add_item(AccountSelectForCampaign(self.accounts, campaign_type, self.discord_id))
        await interaction.response.edit_message(content="Select the account to use:", view=view)

class AccountSelectForCampaign(discord.ui.Select):
    def __init__(self, accounts: list, campaign_type: str, discord_id: str):
        options = []
        for acc in accounts:
            label = f"{acc['username']} ({acc['discord_user_id']})"
            options.append(discord.SelectOption(label=label[:100], value=acc["id"]))
        super().__init__(placeholder="Choose account", min_values=1, max_values=1, options=options)
        self.campaign_type = campaign_type
        self.discord_id = discord_id

    async def callback(self, interaction: discord.Interaction):
        account_id = self.values[0]
        if self.campaign_type == "channel":
            await interaction.response.send_modal(NewCampaignModal("Channel Messaging", account_id))
        else:
            plan, limits = await get_effective_plan(self.discord_id)
            if not limits["dm"]:
                await interaction.response.send_message("DM Auto-Reply requires V3+ or Lifetime plan.", ephemeral=True)
                return
            await interaction.response.send_modal(NewCampaignModal("DM Auto-Reply", account_id))