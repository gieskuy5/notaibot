import requests
import time
import datetime
import json
import logging
import random
import asyncio
import aiohttp
from ratelimit import limits, sleep_and_retry
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import List, Optional, Dict
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Konfigurasi logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BASE_URL = "https://api.notai.com"
CAMPAIGN_ID = os.getenv("CAMPAIGN_ID", "d6a146d0-092b-4206-8b02-8d00c05d7a89")

@dataclass
class UserStats:
    username: str
    initial_level: Optional[int] = None
    final_level: Optional[int] = None
    damage_upgrades: int = 0
    limit_upgrades: int = 0
    missions_completed: int = 0
    taps_performed: int = 0

# Rate limiting: 5 calls per 10 seconds
@sleep_and_retry
@limits(calls=5, period=10)
def rate_limited_api_call(method, *args, **kwargs):
    return method(*args, **kwargs)

def get_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "*/*",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    }

async def login(session, token):
    url = f"{BASE_URL}/scoreboard/me"
    headers = get_headers(token)

    try:
        async with session.get(url, headers=headers) as response:
            response.raise_for_status()
            user_data = await response.json()
            if 'user' in user_data and 'nickname' in user_data['user']:
                logger.info(f"Login successful for user: {user_data['user']['nickname']}")
                return user_data['user']['nickname']
            else:
                logger.warning(f"Login successful, but no nickname found in response")
                return None
    except Exception as err:
        logger.error(f"Login failed: {err}")
        return None

async def get_campaign_missions(session, token, campaign_id):
    url = f"{BASE_URL}/missions"
    current_time = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
    params = {
        "filter[progress]": "true",
        "filter[rewards]": "true",
        "filter[completedPercent]": "true", 
        "filter[hidden]": "false",
        "filter[campaignId]": campaign_id,
        "filter[date]": current_time,
        "filter[grouped]": "true",
        "filter[status]": "AVAILABLE",
        "filter[excludeCategories]": ["REFERRALS", "ACHIEVEMENTS"]
    }
    headers = get_headers(token)

    try:
        async with session.get(url, headers=headers, params=params) as response:
            response.raise_for_status()
            missions = (await response.json())['data']
            logger.info(f"Retrieved {len(missions)} missions for campaign")
            return missions
    except Exception as err:
        logger.error(f"Failed to get missions for campaign: {err}")
        return []

async def complete_mission(session, token, mission_id, mission_label):
    url = f"{BASE_URL}/mission-activity/{mission_id}"
    headers = get_headers(token)

    try:
        async with session.post(url, headers=headers) as response:
            response.raise_for_status()
            result = await response.json()
            if result.get('success'):
                logger.info(f"Successfully completed mission: {mission_label}")
                return True
            else:
                logger.info(f"Unable to complete mission: {mission_label}")
                return False
    except Exception:
        logger.error(f"Error completing mission {mission_label}")
        return False

async def claim_mission_reward(session, token, mission_id, mission_label):
    url = f"{BASE_URL}/mission-reward/{mission_id}"
    headers = get_headers(token)

    try:
        async with session.post(url, headers=headers) as response:
            if response.status == 201: 
                logger.info(f"Claimed reward for mission: {mission_label}")
                return True
            else:
                logger.info(f"Unable to claim reward for mission: {mission_label}")
                return False
    except Exception as err:
        logger.error(f"Error claiming reward for mission {mission_label}: {err}")
        return False

async def process_missions(session, token, campaign_id, user_stats):
    missions = await get_campaign_missions(session, token, campaign_id)
    for mission in missions:
        mission_id = mission['id'] 
        mission_label = mission['label']
        logger.info(f"Processing mission: {mission_label}")
        
        if mission['completedPercent'] != "100":
            if await complete_mission(session, token, mission_id, mission_label):
                if await claim_mission_reward(session, token, mission_id, mission_label):
                    user_stats.missions_completed += 1
        else:
            logger.info(f"Mission already completed: {mission_label}")
        
        await asyncio.sleep(5)  # Wait for 5 seconds before processing the next mission

async def claim_daily(session, token):
    url = f"{BASE_URL}/daily-rewards/today-info"
    headers = get_headers(token)

    try:
        async with session.get(url, headers=headers) as response:
            response.raise_for_status()
            daily_info = await response.json()

            if daily_info["todayClaimed"]:
                logger.info("Daily reward already claimed today.")
                return
        
        claim_url = f"{BASE_URL}/daily-rewards/claim"
        async with session.post(claim_url, headers=headers) as claim_response:
            claim_response.raise_for_status()
            logger.info("Daily reward claimed successfully")
    except Exception as err:
        logger.error(f"Failed to claim daily reward: {err}")

async def get_levels(session, token):
    url = f"{BASE_URL}/levels"
    headers = get_headers(token)
    
    try:
        async with session.get(url, headers=headers) as response:
            response.raise_for_status()
            levels = await response.json()
            return levels
    except Exception as err:
        logger.error(f"Failed to get levels: {err}")
        return []

async def get_tapping_upgrades(session, token):
    url = f"{BASE_URL}/boost/card?filter[type]=CLICKER_BOOSTER"
    headers = get_headers(token)
    
    try:
        async with session.get(url, headers=headers) as response:
            response.raise_for_status()
            upgrades = (await response.json())['data']
            return upgrades
    except Exception as err:
        logger.error(f"Failed to get tapping upgrades: {err}")
        return []

async def upgrade_level(session, token, user_stats):
    url = f"{BASE_URL}/boost/level/purchase"
    headers = get_headers(token)
    
    try:
        async with session.post(url, headers=headers) as response:
            if response.status == 201:
                result = await response.json()
                logger.info(f"Level upgraded successfully. Current level: {result['level']}")
                user_stats.final_level = result['level']
                return True, result['level']
            elif response.status == 402:
                logger.info("Insufficient funds for level upgrade")
                return False, None
            else:
                logger.info("Unable to upgrade level")
                return False, None
    except Exception as err:
        logger.error(f"Error upgrading level: {err}")
        return False, None

async def upgrade_tapping(session, token, upgrade_id, upgrade_type, user_stats):
    url = f"{BASE_URL}/boost/purchase/{upgrade_id}"
    headers = get_headers(token)
    
    try:
        async with session.post(url, headers=headers) as response:
            if response.status == 201:
                result = await response.json()
                logger.info(f"{upgrade_type} tapping upgrade successful")
                if upgrade_type == "Damage":
                    user_stats.damage_upgrades += 1
                elif upgrade_type == "Limit energy":
                    user_stats.limit_upgrades += 1
                return True, result['level']
            elif response.status == 402:
                logger.info(f"Insufficient funds for {upgrade_type} tapping upgrade")
                return False, None
            else:
                logger.info(f"Unable to upgrade {upgrade_type} tapping")
                return False, None
    except Exception as err:
        logger.error(f"Error upgrading {upgrade_type} tapping: {err}")
        return False, None

async def get_game_status(session, token):
    url = f"{BASE_URL}/game-clicker/submit"
    headers = get_headers(token)
    payload = {"clickedCount": 0}  # We're not actually tapping, just checking status
    
    try:
        async with session.post(url, headers=headers, json=payload) as response:
            response.raise_for_status()
            status = await response.json()
            logger.info(f"Current clicks: {status.get('currentClickedCount', 'N/A')}, Total limit: {status.get('totalClicksLimit', 'N/A')}")
            return status
    except Exception as err:
        logger.error(f"Error getting game status: {err}")
        return None

async def perform_tapping(session, token, click_count=15):
    url = f"{BASE_URL}/game-clicker/submit"
    headers = get_headers(token)
    payload = {"clickedCount": click_count}
    
    try:
        async with session.post(url, headers=headers, json=payload) as response:
            response.raise_for_status()
            result = await response.json()
            logger.info(f"Tapping performed successfully. New click count: {result.get('currentClickedCount', 'N/A')}")
            return result
    except Exception as err:
        logger.error(f"Error performing tapping: {err}")
        return None

async def freeze_game(session, token):
    url = f"{BASE_URL}/game-clicker/freeze"
    headers = get_headers(token)
    
    try:
        async with session.post(url, headers=headers) as response:
            response.raise_for_status()
            result = await response.json()
            logger.info("Game frozen successfully")
            return result
    except Exception as err:
        logger.error(f"Error freezing game: {err}")
        return None

async def get_active_boosts(session, token):
    url = f"{BASE_URL}/boost-modification/active"
    headers = get_headers(token)
    
    try:
        async with session.get(url, headers=headers) as response:
            response.raise_for_status()
            active_boosts = await response.json()
            logger.info(f"Active boosts: {[boost['boostModification']['type'] for boost in active_boosts]}")
            return active_boosts
    except Exception as err:
        logger.error(f"Error getting active boosts: {err}")
        return []

async def use_boost(session, token, boost_id, boost_type):
    active_boosts = await get_active_boosts(session, token)
    if any(boost['boostModification']['type'] == boost_type for boost in active_boosts):
        logger.info(f"{boost_type} boost is already active. Skipping.")
        return False

    url = f"{BASE_URL}/boost-modification/buy"
    headers = get_headers(token)
    payload = {"boostModificationId": boost_id}
    
    try:
        async with session.post(url, headers=headers, json=payload) as response:
            response.raise_for_status()
            logger.info(f"Successfully activated {boost_type} boost")
            return True
    except Exception as err:
        logger.info(f"Unable to activate {boost_type} boost: {err}")
        return False

async def get_boosts(session, token):
    url = f"{BASE_URL}/boost-modification"
    headers = get_headers(token)
    
    try:
        async with session.get(url, headers=headers) as response:
            response.raise_for_status()
            return (await response.json())['data']
    except Exception as err:
        logger.error(f"Error getting boosts: {err}")
        return []

async def get_refill_boost(session, token):
    boosts = await get_boosts(session, token)
    refill_boost = next((b for b in boosts if b['type'] == 'REFILL_ENERGY' and b['available'] > 0), None)
    return refill_boost

async def use_refill_boost(session, token):
    refill_boost = await get_refill_boost(session, token)
    if refill_boost:
        url = f"{BASE_URL}/boost-modification/buy"
        headers = get_headers(token)
        payload = {"boostModificationId": refill_boost['id']}
        
        try:
            async with session.post(url, headers=headers, json=payload) as response:
                response.raise_for_status()
                result = await response.json()
                logger.info(f"Successfully used refill boost. New boost ID: {result['id']}")
                return True
        except Exception as err:
            logger.error(f"Error using refill boost: {err}")
            return False
    else:
        logger.warning("No refill boost available.")
        return False

async def auto_play_game(session, token, user_stats, refill_threshold=0.875):
    tapping_count = 0
    consecutive_failures = 0
    max_consecutive_failures = 5
    last_boost_check = time.time()
    boost_check_interval = 300  # 5 minutes in seconds

    while True:
        current_time = time.time()

        if current_time - last_boost_check >= boost_check_interval:
            await use_available_boosts(session, token)
            last_boost_check = current_time

        status = await get_game_status(session, token)
        if status is None:
            consecutive_failures += 1
            if consecutive_failures >= max_consecutive_failures:
                logger.error(f"Failed to get game status {max_consecutive_failures} times in a row. Stopping auto-play.")
                return
            logger.warning(f"Failed to get game status. Retrying... (Attempt {consecutive_failures})")
            await asyncio.sleep(5)
            continue

        current_clicks = status['currentClickedCount']
        total_limit = status['totalClicksLimit']
        
        # Check if current clicks are near the limit
        if current_clicks >= total_limit * refill_threshold:
            logger.info(f"Approaching click limit. Current: {current_clicks}, Limit: {total_limit}. Attempting to use refill...")
            if await use_refill_boost(session, token):
                logger.info("Successfully used refill boost. Continuing tapping.")
                await asyncio.sleep(2)  # Short delay to allow the refill to take effect
                continue
            else:
                logger.info("No refill boost available and approaching limit. Switching to next account.")
                return  # Exit the function to switch to next account

        if current_clicks >= total_limit:
            logger.info("Reached tapping limit. Freezing game and stopping auto-play.")
            await freeze_game(session, token)
            return

        result = await perform_tapping(session, token)
        if result:
            new_click_count = result['currentClickedCount']
            taps_performed = new_click_count - current_clicks
            tapping_count += taps_performed
            user_stats.taps_performed += taps_performed
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            if consecutive_failures >= max_consecutive_failures:
                logger.error(f"Failed to perform tapping {max_consecutive_failures} times in a row. Stopping auto-play.")
                return
            logger.warning(f"Failed to perform tapping. Retrying... (Attempt {consecutive_failures})")

        if tapping_count % 100 == 0:
            logger.info(f"Performed {tapping_count} taps")

        # Add a small random delay to avoid detection
        await asyncio.sleep(0.8 + random.random() * 0.4)  # Sleep between 0.8 and 1.2 seconds

async def use_available_boosts(session, token):
    boosts = await get_boosts(session, token)
    for boost in boosts:
        if boost['available'] > 0:
            await use_boost(session, token, boost['id'], boost['type'])
            await asyncio.sleep(1)  # Short delay between activating boosts

async def process_account(session, token, auto_upgrade, upgrade_count, auto_upgrade_tapping, damage_upgrade_count, limit_upgrade_count, auto_tapping):
    username = await login(session, token)
    if not username:
        logger.error(f"Failed to log in with provided token")
        return

    user_stats = UserStats(username=username)

    try:
        await claim_daily(session, token)
        await asyncio.sleep(2)
        await process_missions(session, token, CAMPAIGN_ID, user_stats)
        
        if auto_upgrade.lower() == 'y':
            levels = await get_levels(session, token)
            if levels:
                user_stats.initial_level = levels[-1]['level']
                user_stats.final_level = user_stats.initial_level
                for i in range(upgrade_count):
                    success, new_level = await upgrade_level(session, token, user_stats)
                    if success:
                        user_stats.final_level = new_level
                    else:
                        break
                    await asyncio.sleep(2)
        
        if auto_upgrade_tapping.lower() == 'y':
            upgrades = await get_tapping_upgrades(session, token)
            if upgrades:
                damage_upgrade = next((u for u in upgrades if u['boostType'] == 'CLICKER_DAMAGE'), None)
                limit_upgrade = next((u for u in upgrades if u['boostType'] == 'CLICKER_ENERGY'), None)
                
                if damage_upgrade:
                    for i in range(damage_upgrade_count):
                        success, _ = await upgrade_tapping(session, token, damage_upgrade['id'], "Damage", user_stats)
                        if not success:
                            break
                        await asyncio.sleep(2)
                
                if limit_upgrade:
                    for i in range(limit_upgrade_count):
                        success, _ = await upgrade_tapping(session, token, limit_upgrade['id'], "Limit energy", user_stats)
                        if not success:
                            break
                        await asyncio.sleep(2)
        
        summarize_upgrades(user_stats)

        if auto_tapping.lower() == 'y':
            logger.info("Starting auto-play...")
            await auto_play_game(session, token, user_stats)
    
    except Exception as e:
        logger.error(f"Error processing account {username}: {e}")
    
    logger.info("Finished processing account. Waiting before next account...")
    await asyncio.sleep(10)

def summarize_upgrades(user_stats):
    logger.info(f"Account: {user_stats.username}")
    if user_stats.initial_level is not None and user_stats.final_level is not None:
        logger.info(f"Level Upgrades: {user_stats.initial_level} -> {user_stats.final_level}")
    logger.info(f"Damage Tapping Upgrades: {user_stats.damage_upgrades}")
    logger.info(f"Limit Energy Tapping Upgrades: {user_stats.limit_upgrades}")
    logger.info(f"Missions Completed: {user_stats.missions_completed}")
    logger.info(f"Total Taps Performed: {user_stats.taps_performed}")

async def process_accounts(auto_upgrade, upgrade_count, auto_upgrade_tapping, damage_upgrade_count, limit_upgrade_count, auto_tapping):
    try:
        with open("token.txt", "r") as file:
            tokens = file.readlines()
    except FileNotFoundError:
        logger.error("File token.txt tidak ditemukan.")
        return
    except IOError as e:
        logger.error(f"Error membaca file token.txt: {e}")
        return
    
    async with aiohttp.ClientSession() as session:
        for token in tokens:
            token = token.strip()
            await process_account(session, token, auto_upgrade, upgrade_count, auto_upgrade_tapping, damage_upgrade_count, limit_upgrade_count, auto_tapping)

def validate_input(prompt, valid_options=None, is_int=False):
    while True:
        user_input = input(prompt).strip()
        if valid_options and user_input.lower() not in valid_options:
            print(f"Invalid input. Please enter one of {', '.join(valid_options)}")
        elif is_int:
            try:
                return int(user_input)
            except ValueError:
                print("Invalid input. Please enter a number.")
        else:
            return user_input

if __name__ == "__main__":
    auto_upgrade = validate_input("Upgrade Level (Y/N): ", ['y', 'n'])
    upgrade_count = 1
    if auto_upgrade.lower() == 'y':
        upgrade_count = validate_input("Enter the number of times to upgrade level: ", is_int=True)
    
    auto_upgrade_tapping = validate_input("Auto Upgrade Tapping (Y/N): ", ['y', 'n'])
    damage_upgrade_count = limit_upgrade_count = 0
    if auto_upgrade_tapping.lower() == 'y':
        damage_upgrade_count = validate_input("Damage Upgrade (number of levels to upgrade): ", is_int=True)
        limit_upgrade_count = validate_input("Limit Upgrade (number of levels to upgrade): ", is_int=True)
    
    auto_tapping = validate_input("Auto Tapping (Y/N): ", ['y', 'n'])
    
    asyncio.run(process_accounts(auto_upgrade, upgrade_count, auto_upgrade_tapping, damage_upgrade_count, limit_upgrade_count, auto_tapping))
