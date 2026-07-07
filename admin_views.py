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