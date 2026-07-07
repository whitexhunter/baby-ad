import discord
import asyncio
import os
import zipfile
import io
import shutil
import datetime
import uuid
import logging
logging.basicConfig(level=logging.INFO)

from storage import get_users, get_accounts, get_campaigns, get_subscriptions, get_keys, save_subscriptions
from views import PanelView, RedeemModal, get_effective_plan
from admin_views import AdminPanelView, GenKeyModal
import campaign_engine

ADMIN_IDS = []
if os.path.exists("admin_ids.txt"):
    with open("admin_ids.txt", "r") as f:
        ADMIN_IDS = [line.strip() for line in f if line.strip()]

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = discord.Bot(intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    campaign_engine.start_engine()
    await campaign_engine.resume_running_campaigns()
    # Immediate expiry check
    await campaign_engine.engine._expiry_checker()

@bot.slash_command(name="panel", description="Open your user dashboard")
async def panel(ctx: discord.ApplicationContext):
    view = PanelView(str(ctx.author.id))
    await view.add_full_buttons()
    embed = await view.get_embed()
    await ctx.respond(embed=embed, view=view, ephemeral=True)

@bot.slash_command(name="admin", description="Admin dashboard")
async def admin(ctx: discord.ApplicationContext):
    if str(ctx.author.id) not in ADMIN_IDS:
        await ctx.respond("You are not an admin.", ephemeral=True)
        return
    view = AdminPanelView()
    embed = discord.Embed(title="Admin Dashboard", color=0xffa500)
    embed.add_field(name="Use the buttons below", value="Select an action", inline=False)
    await ctx.respond(embed=embed, view=view, ephemeral=True)

@bot.slash_command(name="redeem", description="Redeem a license key")
async def redeem(ctx: discord.ApplicationContext):
    await ctx.send_modal(RedeemModal())

@bot.slash_command(name="genkey", description="Generate a new license key (admin only)")
async def genkey(ctx: discord.ApplicationContext):
    if str(ctx.author.id) not in ADMIN_IDS:
        await ctx.respond("You are not an admin.", ephemeral=True)
        return
    await ctx.send_modal(GenKeyModal())

@bot.slash_command(name="extend", description="Extend or create a subscription for a user (admin only)")
async def extend(
    ctx: discord.ApplicationContext,
    user: discord.Option(discord.User, "User to extend"),
    plan: discord.Option(str, "Plan (v1/v2/v3/lifetime)"),
    days: discord.Option(int, "Number of days (0 for lifetime)", min_value=0, max_value=365)
):
    if str(ctx.author.id) not in ADMIN_IDS:
        await ctx.respond("You are not an admin.", ephemeral=True)
        return
    if plan not in ("v1", "v2", "v3", "lifetime"):
        await ctx.respond("Invalid plan. Use v1, v2, v3, or lifetime.", ephemeral=True)
        return
    discord_id = str(user.id)
    subs = await get_subscriptions()
    for s in subs:
        if s["discord_id"] == discord_id and s["status"] == "confirmed":
            s["status"] = "expired"
    if plan == "lifetime" or days == 0:
        expires_at = datetime.datetime(2099, 12, 31, 23, 59, 59, tzinfo=datetime.UTC)
    else:
        expires_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=days)
    sub = {
        "id": str(uuid.uuid4()),
        "discord_id": discord_id,
        "plan": plan,
        "amount": 0,
        "status": "confirmed",
        "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "expires_at": expires_at.isoformat()
    }
    subs.append(sub)
    await save_subscriptions(subs)
    await ctx.respond(f"✅ Subscription extended for {user.mention} with {plan.upper()} plan, expires at {expires_at}.", ephemeral=True)

@bot.slash_command(name="backup", description="Create a full data backup (admin only)")
async def backup(ctx: discord.ApplicationContext):
    if str(ctx.author.id) not in ADMIN_IDS:
        await ctx.respond("You are not an admin.", ephemeral=True)
        return
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
    await ctx.respond(file=discord.File(zip_buffer, f"backup_{timestamp}.zip"), ephemeral=True)

@bot.slash_command(name="restore", description="Restore data from a backup ZIP (admin only)")
async def restore(ctx: discord.ApplicationContext, file: discord.Option(discord.Attachment, "ZIP backup file")):
    if str(ctx.author.id) not in ADMIN_IDS:
        await ctx.respond("You are not an admin.", ephemeral=True)
        return
    if not file.filename.endswith(".zip"):
        await ctx.respond("Please upload a ZIP file.", ephemeral=True)
        return
    data = await file.read()
    safety_dir = f"data/safety_backup_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    os.makedirs(safety_dir, exist_ok=True)
    for fname in ["users.json", "accounts.json", "campaigns.json", "subscriptions.json", "keys.json", "fernet.key"]:
        src = f"data/{fname}"
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(safety_dir, fname))
    try:
        with zipfile.ZipFile(io.BytesIO(data), "r") as zipf:
            zipf.extractall("data")
    except Exception as e:
        await ctx.respond(f"Failed to extract backup: {e}", ephemeral=True)
        return
    await ctx.respond("✅ Backup restored successfully. Safety backup saved in `data/safety_backup_*`", ephemeral=True)

if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise ValueError("DISCORD_TOKEN environment variable not set")
    bot.run(token)