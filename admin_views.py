import discord
import uuid
import datetime
import zipfile
import io
import os
import shutil
import asyncio

from storage import (
    get_users, get_accounts, get_campaigns, get_subscriptions, get_keys,
    save_users, save_accounts, save_campaigns, save_subscriptions, save_keys
)
from views import get_effective_plan


class AdminPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Overview", style=discord.ButtonStyle.blurple, row=0)
    async def overview_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        users = await get_users()
        accounts = await get_accounts()
        campaigns = await get_campaigns()
        subs = await get_subscriptions()
        keys = await get_keys()
        total_users = len(users)
        total_accounts = len(accounts)
        total_campaigns = len(campaigns)
        running = sum(1 for c in campaigns if c["status"] == "running")
        paused = sum(1 for c in campaigns if c["status"] == "paused")
        completed = sum(1 for c in campaigns if c["status"] == "completed")
        failed = sum(1 for c in campaigns if c["status"] == "failed")
        total_revenue = sum(s.get("amount", 0) for s in subs if s["status"] == "confirmed")
        total_keys = len(keys)
        redeemed = sum(1 for k in keys if k.get("redeemed_by"))

        embed = discord.Embed(title="Admin Overview", color=0xffa500)
        embed.add_field(name="Total Users", value=total_users, inline=True)
        embed.add_field(name="Total Accounts", value=total_accounts, inline=True)
        embed.add_field(name="Total Campaigns", value=total_campaigns, inline=True)
        embed.add_field(name="Campaigns (R/P/C/F)", value=f"{running}/{paused}/{completed}/{failed}", inline=False)
        embed.add_field(name="Total Revenue", value=f"${total_revenue}", inline=True)
        embed.add_field(name="Keys Generated / Redeemed", value=f"{total_keys} / {redeemed}", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Users", style=discord.ButtonStyle.blurple, row=0)
    async def users_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        users = await get_users()
        accounts = await get_accounts()
        embed = discord.Embed(title="Users List", color=0x00aaff)
        for uid, data in list(users.items())[:25]:
            plan, _ = await get_effective_plan(uid)
            acc_count = len([a for a in accounts if a["discord_id"] == uid])
            trial = "✅" if data.get("trial_active") else "❌"
            embed.add_field(
                name=f"<@{uid}>",
                value=f"Plan: {plan.upper()}\nAccounts: {acc_count}\nTrial: {trial}",
                inline=False
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Campaigns", style=discord.ButtonStyle.blurple, row=0)
    async def campaigns_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        campaigns = await get_campaigns()
        embed = discord.Embed(title="All Campaigns", color=0x00aaff)
        for c in campaigns[:25]:
            embed.add_field(
                name=f"{c['name']} ({c['type']})",
                value=f"Owner: <@{c['discord_id']}>\nStatus: {c['status']}\nSent/Failed: {c.get('messages_sent', 0)}/{c.get('messages_failed', 0)}",
                inline=False
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Revenue", style=discord.ButtonStyle.blurple, row=0)
    async def revenue_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        subs = await get_subscriptions()
        confirmed = [s for s in subs if s["status"] == "confirmed"]
        embed = discord.Embed(title="Revenue Details", color=0x00ff00)
        total = 0
        for s in confirmed[:25]:
            total += s.get("amount", 0)
            embed.add_field(
                name=f"User <@{s['discord_id']}>",
                value=f"Plan: {s['plan']}\nAmount: ${s.get('amount', 0)}\nExpires: {s['expires_at']}",
                inline=False
            )
        embed.add_field(name="Total Revenue", value=f"${total}", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="System", style=discord.ButtonStyle.blurple, row=0)
    async def system_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        keys = await get_keys()
        campaigns = await get_campaigns()
        subs = await get_subscriptions()
        embed = discord.Embed(title="System Info", color=0x9b59b6)
        embed.add_field(name="Total Keys", value=len(keys), inline=True)
        embed.add_field(name="Total Campaigns", value=len(campaigns), inline=True)
        embed.add_field(name="Total Subscriptions", value=len(subs), inline=True)
        view = discord.ui.View()
        view.add_item(DeleteAllDataButton())
        view.add_item(DeleteAllUsersButton())
        view.add_item(DeleteAllKeysButton())
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="Gen Key", style=discord.ButtonStyle.success, row=1)
    async def gen_key_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.send_modal(GenKeyModal())

    @discord.ui.button(label="Backup", style=discord.ButtonStyle.green, row=1)
    async def backup_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
            for filename in ["users.json", "accounts.json", "campaigns.json", "subscriptions.json", "keys.json", "fernet.key"]:
                path = f"data/{filename}"
                if os.path.exists(path):
                    zipf.write(path, filename)
            manifest = f"Backup created at {datetime.datetime.now(datetime.UTC).isoformat()}\n"
            for fname in os.listdir("data"):
                if fname.endswith(".json") or fname == "fernet.key":
                    size = os.path.getsize(f"data/{fname}")
                    manifest += f"{fname}: {size} bytes\n"
            zipf.writestr("manifest.txt", manifest)
        zip_buffer.seek(0)
        await interaction.response.send_message(file=discord.File(zip_buffer, f"backup_{timestamp}.zip"), ephemeral=True)

    @discord.ui.button(label="Restore", style=discord.ButtonStyle.red, row=1)
    async def restore_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.send_message("Please upload the backup ZIP file using the `/restore` command with a file attachment.", ephemeral=True)

    @discord.ui.button(label="Manage Users", style=discord.ButtonStyle.blurple, row=2)
    async def manage_users_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        users = await get_users()
        if not users:
            await interaction.response.send_message("No users found.", ephemeral=True)
            return
        
        embed = discord.Embed(title="User Management", color=0x00aaff)
        for uid in list(users.keys())[:25]:
            plan, _ = await get_effective_plan(uid)
            embed.add_field(
                name=f"<@{uid}>",
                value=f"Plan: {plan.upper()}\nID: `{uid}`",
                inline=False
            )
        
        view = discord.ui.View()
        view.add_item(SelectUserForManagement(list(users.keys())))
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="Manage Keys", style=discord.ButtonStyle.blurple, row=2)
    async def manage_keys_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        keys = await get_keys()
        if not keys:
            await interaction.response.send_message("No keys found.", ephemeral=True)
            return
        
        embed = discord.Embed(title="Key Management", color=0x00aaff)
        total = len(keys)
        redeemed = sum(1 for k in keys if k.get("redeemed_by"))
        embed.add_field(name="Total Keys", value=total, inline=True)
        embed.add_field(name="Redeemed", value=redeemed, inline=True)
        embed.add_field(name="Available", value=total - redeemed, inline=True)
        
        view = discord.ui.View()
        view.add_item(SelectKeyForManagement(keys))
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class GenKeyModal(discord.ui.Modal):
    def __init__(self, *args, **kwargs):
        super().__init__(title="Generate License Key", *args, **kwargs)
        self.add_item(discord.ui.InputText(label="Plan", placeholder="v1, v2, v3, or lifetime", style=discord.InputTextStyle.short))

    async def callback(self, interaction: discord.Interaction):
        plan = self.children[0].value.strip().lower()
        if plan not in ("v1", "v2", "v3", "lifetime"):
            await interaction.response.send_message("Invalid plan. Use v1, v2, v3, or lifetime.", ephemeral=True)
            return

        import secrets
        key = "HUNTER-" + "-".join(secrets.token_hex(2).upper() for _ in range(3))
        keys = await get_keys()
        keys.append({
            "key": key,
            "plan": plan,
            "created_by": str(interaction.user.id),
            "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "redeemed_by": None,
            "redeemed_at": None
        })
        await save_keys(keys)
        await interaction.response.send_message(f"✅ Key generated: `{key}`", ephemeral=True)


class SelectUserForManagement(discord.ui.Select):
    def __init__(self, user_ids: list):
        options = []
        for uid in user_ids[:25]:
            options.append(discord.SelectOption(
                label=f"{uid[:20]}...",
                value=uid,
                description=f"User ID: {uid[:10]}..."
            ))
        super().__init__(placeholder="Select user to manage", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        uid = self.values[0]
        subs = await get_subscriptions()
        user_subs = [s for s in subs if s["discord_id"] == uid]
        accounts = await get_accounts()
        user_accounts = [a for a in accounts if a["discord_id"] == uid]
        
        embed = discord.Embed(title=f"Managing User", color=0x00ff00)
        embed.add_field(name="User ID", value=uid, inline=False)
        embed.add_field(name="Accounts", value=len(user_accounts), inline=True)
        
        plan, _ = await get_effective_plan(uid)
        embed.add_field(name="Current Plan", value=plan.upper(), inline=True)
        
        for s in user_subs:
            if s["status"] == "confirmed":
                embed.add_field(
                    name=f"Subscription ({s['plan']})",
                    value=f"Expires: {s['expires_at']}",
                    inline=False
                )
        
        view = discord.ui.View()
        view.add_item(UserEditPlanSelect(uid))
        view.add_item(UserSuspendButton(uid))
        view.add_item(UserDeleteButton(uid))
        view.add_item(UserValidateButton(uid))
        
        await interaction.response.edit_message(embed=embed, view=view)


class UserEditPlanSelect(discord.ui.Select):
    def __init__(self, user_id: str):
        options = [
            discord.SelectOption(label="V1 (1 account)", value="v1"),
            discord.SelectOption(label="V2 (3 accounts)", value="v2"),
            discord.SelectOption(label="V3 (5 accounts, DM)", value="v3"),
            discord.SelectOption(label="Lifetime (all features)", value="lifetime"),
        ]
        super().__init__(placeholder="Change user's plan", min_values=1, max_values=1, options=options)
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        new_plan = self.values[0]
        subs = await get_subscriptions()
        
        # Expire all current subscriptions
        for s in subs:
            if s["discord_id"] == self.user_id and s["status"] == "confirmed":
                s["status"] = "expired"
        
        # Create new subscription
        if new_plan == "lifetime":
            expires_at = datetime.datetime(2099, 12, 31, 23, 59, 59, tzinfo=datetime.UTC)
        else:
            expires_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=30)
        
        new_sub = {
            "id": str(uuid.uuid4()),
            "discord_id": self.user_id,
            "plan": new_plan,
            "amount": 0,
            "status": "confirmed",
            "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "expires_at": expires_at.isoformat()
        }
        subs.append(new_sub)
        await save_subscriptions(subs)
        await interaction.response.send_message(f"✅ User's plan changed to {new_plan.upper()}", ephemeral=True)


class UserSuspendButton(discord.ui.Button):
    def __init__(self, user_id: str):
        super().__init__(label="Suspend Subscription", style=discord.ButtonStyle.danger, row=1)
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        subs = await get_subscriptions()
        for s in subs:
            if s["discord_id"] == self.user_id and s["status"] == "confirmed":
                s["status"] = "expired"
        await save_subscriptions(subs)
        await interaction.response.send_message("✅ User's subscriptions suspended.", ephemeral=True)


class UserDeleteButton(discord.ui.Button):
    def __init__(self, user_id: str):
        super().__init__(label="Delete User Data", style=discord.ButtonStyle.danger, row=1)
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message("⚠️ This will delete ALL user data. Type `CONFIRM` in chat within 10 seconds.", ephemeral=True)
        
        def check(m):
            return m.author == interaction.user and m.content == "CONFIRM"
        
        try:
            await interaction.client.wait_for("message", timeout=10.0, check=check)
        except asyncio.TimeoutError:
            await interaction.followup.send("Confirmation timed out.", ephemeral=True)
            return
        
        # Delete user data
        users = await get_users()
        if self.user_id in users:
            del users[self.user_id]
        await save_users(users)
        
        # Delete accounts
        accounts = await get_accounts()
        accounts = [a for a in accounts if a["discord_id"] != self.user_id]
        await save_accounts(accounts)
        
        # Delete campaigns
        campaigns = await get_campaigns()
        campaigns = [c for c in campaigns if c["discord_id"] != self.user_id]
        await save_campaigns(campaigns)
        
        # Expire subscriptions
        subs = await get_subscriptions()
        for s in subs:
            if s["discord_id"] == self.user_id:
                s["status"] = "expired"
        await save_subscriptions(subs)
        
        await interaction.followup.send("✅ User data deleted.", ephemeral=True)


class UserValidateButton(discord.ui.Button):
    def __init__(self, user_id: str):
        super().__init__(label="Validate All Accounts", style=discord.ButtonStyle.green, row=1)
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        accounts = await get_accounts()
        from discord_api import validate_token
        from crypto_utils import decrypt_token
        
        count = 0
        for acc in accounts:
            if acc["discord_id"] == self.user_id:
                try:
                    token = decrypt_token(acc["encrypted_token"])
                    user_info = await validate_token(token)
                    acc["valid"] = user_info is not None
                    if user_info:
                        acc["username"] = user_info.get("username", acc["username"])
                        acc["discord_user_id"] = user_info.get("id", acc["discord_user_id"])
                        count += 1
                except:
                    acc["valid"] = False
        await save_accounts(accounts)
        await interaction.response.send_message(f"✅ Validated {count} accounts for user.", ephemeral=True)


class SelectKeyForManagement(discord.ui.Select):
    def __init__(self, keys: list):
        options = []
        for k in keys[:25]:
            status = "✅ Redeemed" if k.get("redeemed_by") else "🟢 Available"
            label = f"{k['key']} ({k['plan']})"
            options.append(discord.SelectOption(label=label[:100], value=k["key"], description=status))
        super().__init__(placeholder="Select key to manage", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        key_str = self.values[0]
        keys = await get_keys()
        key_obj = next((k for k in keys if k["key"] == key_str), None)
        if not key_obj:
            await interaction.response.send_message("Key not found.", ephemeral=True)
            return
        
        embed = discord.Embed(title=f"Managing Key", color=0x00aaff)
        embed.add_field(name="Key", value=f"`{key_str}`", inline=False)
        embed.add_field(name="Plan", value=key_obj["plan"].upper(), inline=True)
        embed.add_field(name="Created", value=key_obj["created_at"], inline=True)
        embed.add_field(name="Redeemed By", value=key_obj.get("redeemed_by") or "Not redeemed", inline=True)
        
        view = discord.ui.View()
        view.add_item(DeleteKeyButton(key_str))
        if not key_obj.get("redeemed_by"):
            view.add_item(RevokeKeyButton(key_str))
        
        await interaction.response.edit_message(embed=embed, view=view)


class DeleteKeyButton(discord.ui.Button):
    def __init__(self, key_str: str):
        super().__init__(label="Delete Key", style=discord.ButtonStyle.danger, row=0)
        self.key_str = key_str

    async def callback(self, interaction: discord.Interaction):
        keys = await get_keys()
        keys = [k for k in keys if k["key"] != self.key_str]
        await save_keys(keys)
        await interaction.response.send_message("✅ Key deleted.", ephemeral=True)


class RevokeKeyButton(discord.ui.Button):
    def __init__(self, key_str: str):
        super().__init__(label="Revoke (mark as used)", style=discord.ButtonStyle.danger, row=0)
        self.key_str = key_str

    async def callback(self, interaction: discord.Interaction):
        keys = await get_keys()
        for k in keys:
            if k["key"] == self.key_str:
                k["redeemed_by"] = "REVOKED_BY_ADMIN"
                k["redeemed_at"] = datetime.datetime.now(datetime.UTC).isoformat()
                break
        await save_keys(keys)
        await interaction.response.send_message("✅ Key marked as redeemed (revoked).", ephemeral=True)


class DeleteAllDataButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Delete All Data", style=discord.ButtonStyle.danger, row=0)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message("⚠️ This will delete ALL data. Type `CONFIRM` in chat within 10 seconds.", ephemeral=True)
        
        def check(m):
            return m.author == interaction.user and m.content == "CONFIRM"
        
        try:
            await interaction.client.wait_for("message", timeout=10.0, check=check)
        except asyncio.TimeoutError:
            await interaction.followup.send("Confirmation timed out.", ephemeral=True)
            return
        
        for fname in ["users.json", "accounts.json", "campaigns.json", "subscriptions.json", "keys.json"]:
            path = f"data/{fname}"
            if os.path.exists(path):
                os.remove(path)
        await interaction.followup.send("All data deleted.", ephemeral=True)


class DeleteAllUsersButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Delete All Users", style=discord.ButtonStyle.danger, row=1)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message("⚠️ This will delete ALL users data. Type `CONFIRM` in chat within 10 seconds.", ephemeral=True)
        
        def check(m):
            return m.author == interaction.user and m.content == "CONFIRM"
        
        try:
            await interaction.client.wait_for("message", timeout=10.0, check=check)
        except asyncio.TimeoutError:
            await interaction.followup.send("Confirmation timed out.", ephemeral=True)
            return
        
        await save_users({})
        await interaction.followup.send("All users deleted.", ephemeral=True)


class DeleteAllKeysButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Delete All Keys", style=discord.ButtonStyle.danger, row=2)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message("⚠️ This will delete ALL keys. Type `CONFIRM` in chat within 10 seconds.", ephemeral=True)
        
        def check(m):
            return m.author == interaction.user and m.content == "CONFIRM"
        
        try:
            await interaction.client.wait_for("message", timeout=10.0, check=check)
        except asyncio.TimeoutError:
            await interaction.followup.send("Confirmation timed out.", ephemeral=True)
            return
        
        await save_keys([])
        await interaction.followup.send("All keys deleted.", ephemeral=True)
