import logging
from pyrofork import Client, filters
from pyrofork.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrofork.enums import ChatMemberStatus
from pyrofork.errors.exceptions.bad_request_400 import UserNotParticipant
from motor.motor_asyncio import AsyncIOMotorClient
import asyncio

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables
API_ID = ""
API_HASH = ""
BOT_TOKEN = ""
OWNER_ID = "" # Replace with the actual owner ID
CHANNEL_ID =  # Replace with your channel ID
FORCE_SUB_CHANNEL = CHANNEL_ID
MONGO_URI = ""
ADMINS = []
CHANNEL_USERNAME = "" #username 
ADMINS.append(OWNER_ID)


# Initialize the Pyrogram client
app = Client("giveaway_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# MongoDB connection setup
mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client.giveaway_bot
broadcast_db = mongo_client.broadcast_db

giveaway_active = False


async def is_subscribed(user_id):
    if user_id in ADMINS:
        return True

    try:
        member = await app.get_chat_member(chat_id=FORCE_SUB_CHANNEL, user_id=user_id)
        return member.status in [ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER]
    except UserNotParticipant:
        return False
    except Exception as e:
        logger.error(f"Error checking subscription status for user {user_id}: {e}")
        return False


async def register_user(user_id, first_name):
    await db.entries.update_one({"user_id": user_id},
                                {"$set": {"user_id": user_id, "referrals": 0, "username": first_name}}, upsert=True)
    await broadcast_db.users.update_one({"user_id": user_id}, {"$set": {"user_id": user_id, "first_name": first_name}},
                                        upsert=True)
    logger.info(f"User {first_name} ({user_id}) registered successfully.")


async def update_referrer(referrer_id):
    await db.entries.update_one({"user_id": referrer_id}, {"$inc": {"referrals": 1}})


async def get_leaderboard():
    return await db.entries.find().sort("referrals", -1).limit(20).to_list(None)


async def check_user_exists(user_id):
    return await db.entries.find_one({"user_id": user_id}) is not None


async def generate_referral_link(user_id):
    bot_username = (await app.get_me()).username
    return f"https://t.me/{bot_username}?start={user_id}"


@app.on_message(filters.command("start_giveaway") & filters.user(OWNER_ID))
async def start_giveaway(client, message):
    global giveaway_active
    giveaway_active = True
    logger.info(f"Giveaway started by owner {message.from_user.first_name} ({message.from_user.id}).")
    await message.reply_text("The giveaway has started!")


@app.on_message(filters.command("stop_giveaway") & filters.user(OWNER_ID))
async def stop_giveaway(client, message):
    global giveaway_active
    giveaway_active = False
    logger.info(f"Giveaway stopped by owner {message.from_user.first_name} ({message.from_user.id}).")

    top_user = await db.entries.find_one(sort=[("referrals", -1)])
    if top_user:
        winner_name = top_user['username'] or 'Unknown'
        referrals = top_user['referrals']
        await message.reply_text(
            f'The giveaway has ended! The winner is {winner_name} ({top_user["user_id"]}) with {referrals} referrals! Congratulations!')
        await db.entries.delete_many({})
        logger.info("Giveaway ended. Database reset.")
    else:
        await message.reply_text('The giveaway has ended! No entries found.')
        logger.info("Giveaway ended but no entries found.")


@app.on_message(filters.command("start"))
async def register(client, message):
    global giveaway_active
    user = message.from_user
    user_id = user.id
    first_name = user.first_name

    if not giveaway_active:
        await message.reply_text("The giveaway is not active.")
        return

    if len(message.command) == 1:
        if await check_user_exists(user_id):
            ref_link = await generate_referral_link(user_id)
            await message.reply_text(f'You are already registered! Your referral link is: {ref_link}')
            logger.info(f"User {first_name} ({user_id}) requested referral link. Already registered.")
            return

        if not await is_subscribed(user_id):
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton("Join Channel", url=f"https://t.me/{CHANNEL_USERNAME}")]])
            await message.reply_text('You need to join the specified channel to enter the giveaway.',
                                     reply_markup=keyboard)
            logger.info(f"User {first_name} ({user_id}) tried to register without joining the channel.")
            return

        await register_user(user_id, first_name)
        ref_link = await generate_referral_link(user_id)
        await message.reply_text(f'Welcome to the Giveaway Bot! Use your referral link to invite others: {ref_link}')
    else:
        referrer_id = int(message.command[1])

        if not await check_user_exists(referrer_id):
            await message.reply_text('Invalid referral link.')
            logger.info(f"Invalid referral link used by user {first_name} ({user_id}).")
            return

        if await check_user_exists(user_id):
            await message.reply_text('You are already registered in the giveaway.')
            logger.info(f"User {first_name} ({user_id}) tried to re-enter giveaway. Already registered.")
            return

        if not await is_subscribed(user_id):
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton("Join Channel", url=f"https://t.me/{CHANNEL_USERNAME}")]])
            await message.reply_text('You need to join the specified channel to enter the giveaway.',
                                     reply_markup=keyboard)
            logger.info(
                f"User {first_name} ({user_id}) tried to register through referral without joining the channel.")
            return

        await register_user(user_id, first_name)
        await update_referrer(referrer_id)
        referrer_name = (await db.entries.find_one({"user_id": referrer_id}))['username'] or 'Unknown'
        await message.reply_text(
            f'You have successfully entered the giveaway through a referral from {referrer_name}!')
        logger.info(f"User {first_name} ({user_id}) entered giveaway through referral from {referrer_name}.")


@app.on_message(filters.command("refer"))
async def refer_link(client, message):
    user = message.from_user
    user_id = user.id

    if not await check_user_exists(user_id):
        await message.reply_text("You are not registered in the giveaway.")
        logger.info(f"User {user.first_name} ({user_id}) tried to get a referral link but is not registered.")
        return

    ref_link = await generate_referral_link(user_id)
    await message.reply_text(f'Your referral link is: {ref_link}')
    logger.info(f"User {user.first_name} ({user_id}) requested their referral link.")


@app.on_message(filters.command("leaderboard") & filters.user(OWNER_ID))
async def leaderboard(client, message):
    leaderboard = await get_leaderboard()
    response = "Leaderboard:\n"
    for entry in leaderboard:
        name = entry.get('username') or entry.get('first_name', 'Unknown')
        referrals = entry.get('referrals', 0)
        response += f"{name} ({entry['user_id']}): {referrals} referrals\n"
    await message.reply_text(response)
    logger.info(f"Leaderboard requested by owner {message.from_user.first_name} ({message.from_user.id}).")


@app.on_message(filters.command("referrals"))
async def check_referrals(client, message):
    user = message.from_user
    user_entry = await db.entries.find_one({"user_id": user.id})
    if user_entry:
        referrals = user_entry.get('referrals', 0)
        await message.reply_text(f'You have {referrals} referrals.')
        logger.info(f"User {user.first_name} ({user.id}) checked their referrals.")
    else:
        await message.reply_text('You are not registered in the giveaway.')
        logger.info(f"User {user.first_name} ({user.id}) tried to check referrals but is not registered.")


@app.on_message(filters.command("reset") & filters.user(OWNER_ID))
async def reset_database(client, message):
    await db.entries.delete_many({})
    logger.info(f"Database has been reset by owner {message.from_user.first_name} ({message.from_user.id}).")
    await message.reply_text("The database has been reset.")


@app.on_message(filters.command("broadcast") & filters.user(OWNER_ID))
async def broadcast_message(client, message):
    if len(message.command) < 2:
        await message.reply_text("Please provide the message to broadcast.")
        return

    broadcast_text = message.text.split(' ', 1)[1]
    async for user in broadcast_db.users.find():
        try:
            await app.send_message(user['user_id'], broadcast_text)
            logger.info(f"Broadcasted message to {user['first_name']} ({user['user_id']}).")
        except Exception as e:
            logger.error(f"Failed to send message to {user['first_name']} ({user['user_id']}): {e}")


@app.on_message(filters.command("help") & filters.user(OWNER_ID))
async def help_message(client, message):
    help_text = """
Giveaway Bot Commands:
/start_giveaway - Start the giveaway
/stop_giveaway - Stop the giveaway
/leaderboard - Show the leaderboard
/reset - Reset the giveaway database
/broadcast <message> - Broadcast a message to all participants
/refer - Get your referral link
    """
    await message.reply_text(help_text)
    logger.info(f"Help requested by owner {message.from_user.first_name} ({message.from_user.id}).")


async def check_mongo_connection():
    try:
        await mongo_client.admin.command('ping')
        logger.info("Connected to MongoDB successfully.")
        print("Connected to MongoDB successfully.")
    except Exception as e:
        logger.error(f"Error connecting to MongoDB: {e}")
        print(f"Error connecting to MongoDB: {e}")


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(check_mongo_connection())
    app.run()
