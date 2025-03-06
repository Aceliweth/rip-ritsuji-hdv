import threading
import time
import re
import keyboard
import pytesseract
import pyautogui
import colorama
from termcolor import colored
from PIL import ImageGrab, ImageEnhance, Image
import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk

import binascii
import hashlib
import json as jsond
import os
import platform
import subprocess
import sys
from datetime import datetime
from time import sleep
from uuid import uuid4
import requests  # Pour KeyAuth
import asyncio

# Utilisation de pywin32 pour des clics bas niveau (pour Windows)
if os.name == 'nt':
    import win32api, win32con

def win32_click(x, y):
    """Effectue un clic gauche en position (x, y) via l'API Windows."""
    win32api.SetCursorPos((x, y))
    time.sleep(0.1)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
    time.sleep(0.05)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)

# -----------------------------
# Fonctions de sauvegarde de config
# -----------------------------
def load_config():
    if os.path.exists("config.json"):
        with open("config.json", "r") as f:
            return jsond.load(f)
    else:
        return {}

def save_config(config):
    with open("config.json", "w") as f:
        jsond.dump(config, f, indent=4)

def filter_resource_data(resources):
    """
    Retourne une liste de ressources en excluant les champs runtime (inventory_count et bank_count).
    Seuls les champs persistants (name, lot, pickup, desired, sale_enabled) sont enregistrés.
    """
    persistent_resources = []
    for res in resources:
        persistent_resources.append({
            "name": res.get("name"),
            "lot": res.get("lot", 100),
            "pickup": res.get("pickup", 0),
            "desired": res.get("desired", 15),
            "sale_enabled": res.get("sale_enabled", False)
        })
    return persistent_resources

# -----------------------------
# Intégration de discord.py
# -----------------------------
import discord
from discord.ext import commands

discord_intents = discord.Intents.default()
discord_client = commands.Bot(command_prefix="!", intents=discord_intents)

# IMPORTANT : NE PARTAGEZ JAMAIS VOTRE TOKEN !
DISCORD_BOT_TOKEN = "MTM0MDI4NjY0MjUyMjY4NTQ0MQ.Gb-On2.8SZmlnUvbl2jNSwG8e_tL5CvIYLS6SwQfIkkf4"

@discord_client.event
async def on_ready():
    print(f"✅ Discord Bot connecté en tant que {discord_client.user}")

async def send_dm(discord_id: int, message):
    try:
        user = discord_client.get_user(discord_id)
        if user is None:
            user = await discord_client.fetch_user(discord_id)
        if isinstance(message, discord.Embed):
            await user.send(embed=message)
        else:
            await user.send(content=message)
    except Exception as e:
        print(f"Erreur lors de l'envoi du DM via discord.py : {e}")

def start_discord_bot():
    discord_client.run(DISCORD_BOT_TOKEN)

discord_thread = threading.Thread(target=start_discord_bot, daemon=True)
discord_thread.start()

# -----------------------------
# Partie Bot : DofusPriceBot
# -----------------------------
class DofusPriceBot:
    def __init__(self):
        pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        colorama.init()
        
        # Zones de capture
        self.SEARCH_BAR_REGION = (650, 246)
        self.MY_PRICE_REGION = (1033, 322, 1170, 352)
        self.ALL_LOTS_REGION = (230, 683, 413, 804)
        self.CURRENT_LOT_COUNT_REGION = (272, 554, 311, 580)
        
        self.PRICE_INPUT = (278, 342)
        self.VALIDATE_BUTTON = (381, 564)
        self.SELECT_ALL_BUTTON = (1198, 287)
        self.CONFIRM_BUTTON = (1053, 704)

        self.resource_name = ""
        self.resources = []  
        self.running = False
        self.loop_thread = None
        self.debug_mode = False
        self.interval = 20
        self.paused = False
        self.price_drop = 0
        self.use_security = False
        self.discord_id = ""
        self.security_percent = 10

    def calculate_safe_price(self, current_price, lowest_price, drop=0):
        if lowest_price is None:
            return current_price
        if lowest_price >= current_price:
            return current_price
        proposed_price = lowest_price - drop
        if self.use_security:
            min_allowed = int(current_price * (1 - self.security_percent / 100))
            self.log(f"Sécurité prix | Min autorisé : {min_allowed} | Prix suggéré : {proposed_price}", "yellow", "info")
            if proposed_price < min_allowed:
                proposed_price = min_allowed
        if proposed_price >= current_price:
            return current_price
        return proposed_price

    def log(self, message, color="white", prefix=""):
        prefix_map = {"info": "🔍 ", "success": "✅ ", "warning": "⚠️ ", "error": "🚨 "}
        full_prefix = prefix_map.get(prefix, "")
        log_message = f"{full_prefix}{message}"
        print(colored(log_message, color))
        if prefix == "error":
            self.send_private_notification(log_message)

    def preprocess_image(self, image):
        image = image.convert("L")
        enhancer = ImageEnhance.Contrast(image)
        image = enhancer.enhance(3.0)
        image = image.point(lambda x: 0 if x < 250 else 255)
        return image

    def upscale_image(self, image, factor=2):
        new_size = (int(image.width * factor), int(image.height * factor))
        return image.resize(new_size, Image.LANCZOS)

    def get_price_from_region(self, region):
        try:
            screenshot = ImageGrab.grab(bbox=region)
            screenshot = self.upscale_image(screenshot, factor=2)
            screenshot = self.preprocess_image(screenshot)
            custom_config = r"--psm 6 -c tessedit_char_whitelist=0123456789"
            text = pytesseract.image_to_string(screenshot, config=custom_config)
            text = text.replace(" ", "").strip()
            for k, v in {"O": "0", "o": "0", "I": "1", "l": "1", "S": "5", "s": "5", "B": "8", "Z": "7"}.items():
                text = text.replace(k, v)
            match = re.search(r"\d+", text)
            return int(match.group()) if match else None
        except Exception as e:
            self.log(f"Erreur OCR : {str(e)[:50]}", "red", "error")
            return None

    def get_all_lots_text(self):
        try:
            screenshot = ImageGrab.grab(bbox=self.ALL_LOTS_REGION)
            screenshot = self.upscale_image(screenshot, factor=2)
            screenshot = self.preprocess_image(screenshot)
            custom_config = r"--psm 6 -c tessedit_char_whitelist=0123456789\s"
            text = pytesseract.image_to_string(screenshot, config=custom_config)
            print(f"[DEBUG OCR] Texte détecté :\n{text}\n------")
            return text
        except Exception as e:
            self.log(f"Erreur OCR multi-ligne : {e}", "red", "error")
            return ""

    def parse_lots(self, text_ocr):
        lines = [l.strip() for l in text_ocr.split("\n") if l.strip()]
        lots_found = {}
        for line in lines:
            cleaned = re.sub(r"[^\d\s]", "", line).strip()
            match = re.match(r"^(1|10|100)\s+(\d[\d\s]*)$", cleaned)
            if match:
                lot = int(match.group(1))
                price_str = match.group(2).replace(" ", "")
                try:
                    price_val = int(price_str)
                    lots_found[lot] = price_val
                except ValueError:
                    pass
        if not lots_found and len(lines) == 3:
            try:
                lots_found[1] = int(lines[0].replace(" ", ""))
                lots_found[10] = int(lines[1].replace(" ", ""))
                lots_found[100] = int(lines[2].replace(" ", ""))
            except Exception as e:
                self.log(f"Erreur lors du fallback du parsing des lots : {e}", "red", "error")
        return lots_found

    def get_current_prices(self, lot_choice=100):
        my_price = self.get_price_from_region(self.MY_PRICE_REGION)
        text_ocr = self.get_all_lots_text()
        parsed_lots = self.parse_lots(text_ocr)
        lowest_price = parsed_lots.get(lot_choice, None)
        return my_price, lowest_price

    def select_all_items(self):
        pyautogui.click(self.SELECT_ALL_BUTTON)
        time.sleep(1.5)

    def set_new_price(self, old_price, new_price):
        pyautogui.click(self.PRICE_INPUT)
        time.sleep(1.5)
        pyautogui.hotkey("ctrl", "a")
        pyautogui.press("backspace")
        time.sleep(0.5)
        pyautogui.write(str(new_price), interval=0.15)
        time.sleep(1.5)
        pyautogui.click(self.VALIDATE_BUTTON)
        time.sleep(3)
        win32_click(1056, 704)
        time.sleep(2)
        embed = discord.Embed(
            title="Mise à jour du prix réussie !",
            description=f"Le prix de la ressource **{self.resource_name}** a été mis à jour.",
            color=discord.Color.green()
        )
        embed.add_field(name="Ancien prix", value=f"{old_price}", inline=True)
        embed.add_field(name="Nouveau prix", value=f"{new_price}", inline=True)
        embed.timestamp = datetime.utcnow()
        self.send_private_notification(embed)

    def select_resource(self, resource_name, lot_choice=100, actualisation_only=False):
        try:
            self.resource_name = resource_name
            pyautogui.click(self.SEARCH_BAR_REGION)
            time.sleep(1.5)
            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.4)
            pyautogui.press("backspace")
            time.sleep(1)
            pyautogui.write(resource_name, interval=0.15)
            time.sleep(2)
            pyautogui.press("enter")
            time.sleep(3)
            my_price = self.get_price_from_region(self.MY_PRICE_REGION)
            if my_price is None:
                self.log(f"Aucun prix détecté pour {resource_name} - La ressource n'est probablement pas en vente.", "yellow", "warning")
                if actualisation_only:
                    self.log(f"Arrêt de la vérification de {resource_name} lors des prochains cycles.", "yellow", "warning")
                    # Retirer la ressource de la liste pour ne plus la vérifier
                    self.resources = [r for r in self.resources if r.get("name") != resource_name]
                return False
            self.select_all_items()
            my_price, lowest_price = self.get_current_prices(lot_choice)
            if None in (my_price, lowest_price):
                self.log("Échec de lecture - Nouvelle tentative...", "yellow", "warning")
                my_price, lowest_price = self.get_current_prices(lot_choice)
            self.log(f"Analyse | Mon prix : {my_price} | Prix bas : {lowest_price}", "blue", "info")
            # ... suite du code
            
            new_price = self.calculate_safe_price(my_price, lowest_price, self.price_drop)
            
            if my_price != new_price:
                self.log(f"Mise à jour du prix : de {my_price} à {new_price} (-{self.price_drop} kamas)", "green", "success")
                self.set_new_price(my_price, new_price)
                self.log("Prix modifié, sélection conservée.", "green", "success")
            else:
                self.log("Prix compétitif, sélection conservée.", "green", "success")
                self.select_all_items()
            
            if actualisation_only:
                self.log(f"Actualisation uniquement pour {resource_name} terminée.", "green", "success")
            return True
        except Exception as e:
            self.log(f"Erreur lors de la sélection : {e}", "red", "error")
            return False

    def get_current_sale_count(self):
        try:
            screenshot = ImageGrab.grab(bbox=self.CURRENT_LOT_COUNT_REGION)
            screenshot = self.upscale_image(screenshot, factor=2)
            screenshot = self.preprocess_image(screenshot)
            custom_config = r"--psm 6 -c tessedit_char_whitelist=0123456789()"
            text = pytesseract.image_to_string(screenshot, config=custom_config)
            text = text.strip()
            text = re.sub(r"^[A-Za-z\s]+", "", text)
            print(f"[DEBUG OCR] {text}")
            match = re.search(r"\((\d+)\)", text)
            if match:
                return int(match.group(1))
            return 0
        except Exception as e:
            self.log(f"Erreur OCR : {e}", "red", "error")
            return 0

    def pickup_resources(self):
        self.log("Accès au coffre personnel pour récupérer les ressources...", "cyan", "info")
        win32_click(1159, 420)
        time.sleep(1)
        win32_click(1390, 474)
        time.sleep(2)
        for resource in self.resources:
            if resource.get("pickup", 0) > 0 and resource.get("sale_enabled", False):
                bank_count = resource.get("bank_count", 0)
                lot_size = resource.get("lot", 100)
                if bank_count < lot_size:
                    self.log(f"Banque insuffisante pour {resource['name']} (en banque : {bank_count} < lot de {lot_size}).", "yellow", "warning")
                    continue
                pickup_amount = resource.get("pickup", 0)
                amount_to_pick = min(pickup_amount, bank_count)
                self.log(f"Pickup de {resource['name']} x {amount_to_pick} depuis la banque.", "cyan", "info")
                win32_click(386, 170)
                time.sleep(1)
                pyautogui.write(resource["name"], interval=0.15)
                time.sleep(1)
                pyautogui.moveTo(336, 299)
                pyautogui.dragTo(1183, 528, duration=1)
                time.sleep(1)
                pyautogui.write(str(amount_to_pick), interval=0.15)
                time.sleep(1)
                win32_click(1296, 546)
                time.sleep(1)
                resource["inventory_count"] = resource.get("inventory_count", 0) + amount_to_pick
                resource["bank_count"] = bank_count - amount_to_pick
                self.log(f"Après pickup: {resource['name']} - Inventaire : {resource['inventory_count']}, Banque : {resource['bank_count']}", "blue", "info")
        keyboard.press("esc")
        time.sleep(1)

    def pickup_specific_resource(self, resource):
        bank_count = resource.get("bank_count", 0)
        lot_size = resource.get("lot", 100)
        if bank_count < lot_size:
            self.log(f"Banque insuffisante pour {resource['name']} (en banque : {bank_count} < lot de {lot_size}).", "yellow", "warning")
            return
        pickup_amount = resource.get("pickup", 0)
        amount_to_pick = min(pickup_amount, bank_count)
        self.log(f"Pickup de la ressource {resource['name']} depuis le coffre, quantité : {amount_to_pick}.", "cyan", "info")
        win32_click(386, 170)
        time.sleep(1)
        pyautogui.write(resource["name"], interval=0.15)
        time.sleep(1)
        pyautogui.moveTo(336, 299)
        pyautogui.dragTo(1183, 528, duration=1)
        time.sleep(1)
        pyautogui.write(str(amount_to_pick), interval=0.15)
        time.sleep(1)
        win32_click(1296, 546)
        time.sleep(1)
        keyboard.press("esc")
        time.sleep(1)
        resource["inventory_count"] = resource.get("inventory_count", 0) + amount_to_pick
        resource["bank_count"] = bank_count - amount_to_pick
        self.log(f"Après pickup spécifique: {resource['name']} - Inventaire : {resource['inventory_count']}, Banque : {resource['bank_count']}", "blue", "info")

    def get_resource_from_bank(self, resource):
        self.log(f"Accès à la banque pour récupérer {resource['name']}...", "cyan", "info")
        win32_click(1743, 116)
        time.sleep(3)
        win32_click(708, 189)
        time.sleep(3)
        win32_click(1166, 252)
        time.sleep(3)
        win32_click(638, 522)
        time.sleep(3)
        win32_click(1352, 855)
        time.sleep(3)
        win32_click(1084, 766)
        time.sleep(3)
        win32_click(1167, 432)
        time.sleep(3)
        win32_click(1305, 469)
        time.sleep(5)
        self.pickup_specific_resource(resource)
        time.sleep(3)
        win32_click(1451, 117)
        time.sleep(3)
        self.goto_auction_house()
        
    def goto_auction_house(self):
        self.log("Déplacement vers l'hôtel de vente...", "cyan", "info")
        win32_click(1449, 117)
        time.sleep(1)
        win32_click(346, 885)
        time.sleep(3) 
        win32_click(830, 616)
        time.sleep(3) 
        win32_click(972, 247)
        time.sleep(3) 
        win32_click(812, 520)
        time.sleep(3)  
        win32_click(1356, 851)
        time.sleep(3)
        self.log("Accès à l'hôtel de vente...", "cyan", "info")
        win32_click(1529, 360)
        time.sleep(7)
        win32_click(780, 157)
        time.sleep(2)

    def sell_resources(self):
        for resource in self.resources:
            if not (resource.get("pickup", 0) > 0 and resource.get("sale_enabled", False)):
                continue

            if resource.get("inventory_count", 0) <= 0 and resource.get("bank_count", 0) <= 0:
                self.log(f"Pas de ressources disponibles pour {resource['name']} (inventaire et banque vides). Actualisation uniquement.", "yellow", "warning")
                continue

            self.log(f"Actualisation préalable de {resource['name']} pour mise en vente...", "cyan", "info")
            self.select_resource(resource["name"], resource.get("lot", 100), actualisation_only=False)
            
            self.log(f"Début de la mise en vente de {resource['name']}...", "cyan", "info")
            
            sale_slot_region = (458, 934, 546, 978)
            try:
                screenshot = ImageGrab.grab(bbox=sale_slot_region)
                screenshot = self.upscale_image(screenshot, factor=2)
                screenshot = self.preprocess_image(screenshot)
                custom_config = r"--psm 6"
                slots_text = pytesseract.image_to_string(screenshot, config=custom_config).strip()
                match = re.search(r"(\d+)\s*/\s*(\d+)", slots_text)
                if match:
                    current_slots = int(match.group(1))
                    total_slots = int(match.group(2))
                    available_slots = total_slots - current_slots
                    self.log(f"{available_slots} emplacements disponibles dans l'HDV", "blue", "info")
                else:
                    self.log("Impossible de parser les emplacements de vente.", "red", "error")
                    available_slots = 0
            except Exception as e:
                self.log("Erreur lors de la lecture des emplacements de vente.", "red", "error")
                available_slots = 0

            win32_click(1415, 171)
            time.sleep(1)
            pyautogui.write(resource["name"], interval=0.15)
            time.sleep(1)
            win32_click(1391, 357)
            time.sleep(1)
            win32_click(650, 246)
            time.sleep(1)
            pyautogui.write(resource["name"], interval=0.15)
            time.sleep(1)
            
            current_sale_price = self.get_price_from_region(self.MY_PRICE_REGION)
            if current_sale_price is None:
                self.log(f"Aucun prix détecté pour {resource['name']} - La ressource n'est pas actuellement en vente, lancement direct de la mise en vente.", "yellow", "warning")
                sale_price = self.get_current_prices(resource.get("lot", 100))[1]
            else:
                sale_price = current_sale_price
            self.log(f"Prix de vente pour {resource['name']} : {sale_price}", "blue", "info")
            
            while True:
                self.select_all_items()
                time.sleep(1)
                current_lot_count = self.get_current_sale_count()
                self.select_all_items()
                time.sleep(1)
                win32_click(1391, 357)
                time.sleep(1)
                
                self.log(f"{resource['name']} : {current_lot_count} lot(s) en vente (objectif : {resource.get('desired', 15)})", "blue", "info")
                if resource.get("inventory_count", 0) <= 0:
                    self.log(f"Inventaire épuisé pour {resource['name']}.", "yellow", "warning")
                    break
                if current_lot_count >= resource.get("desired", 15):
                    self.log(f"Objectif atteint pour {resource['name']}.", "green", "success")
                    break
                
                lots_needed = resource.get("desired", 15) - current_lot_count
                current_inventory = resource.get("inventory_count", 0)
                available_lots = current_inventory // resource.get("lot", 100)
                required_units = lots_needed * resource.get("lot", 100)
                if current_inventory < required_units:
                    self.log(f"Inventaire insuffisant pour {resource['name']} (disponible: {current_inventory}, requis: {required_units}). Vente du lot disponible.", "yellow", "warning")
                    available_lots = current_inventory // resource.get("lot", 100)
                
                lots_to_sell = min(lots_needed, available_lots, available_slots)
                self.log(f"Mise en vente de {lots_to_sell} lot(s) pour {resource['name']}.", "blue", "info")
                
                win32_click(234, 409)
                time.sleep(1)
                if resource.get("lot", 100) == 1:
                    win32_click(220, 438)
                elif resource.get("lot", 100) == 10:
                    win32_click(221, 465)
                elif resource.get("lot", 100) == 100:
                    win32_click(227, 495)
                time.sleep(1)
                pyautogui.write(str(sale_price), interval=0.15)
                time.sleep(1)
                for i in range(lots_to_sell):
                    win32_click(322, 564)
                    time.sleep(0.5)
                
                sold_units = lots_to_sell * resource.get("lot", 100)
                resource["inventory_count"] = resource.get("inventory_count", 0) - sold_units
                self.log(f"{resource['name']} : {sold_units} unités vendues. Inventaire restant : {resource.get('inventory_count', 0)}", "green", "success")
                time.sleep(3)
        
    def pickup_missing_resources(self, missing_resources):
        self.log("Début de la récupération groupée des ressources manquantes...", "blue", "info")
        win32_click(1743, 116)
        time.sleep(3)
        win32_click(708, 189)
        time.sleep(3)
        win32_click(1166, 252)
        time.sleep(3)
        win32_click(638, 522)
        time.sleep(3)
        win32_click(1352, 855)
        time.sleep(3)
        win32_click(1084, 766)
        time.sleep(3)
        win32_click(1167, 432)
        time.sleep(3)
        win32_click(1305, 469)
        time.sleep(5)
        for resource in missing_resources:
            self.log(f"Récupération de {resource['name']} (manquant {resource.get('pickup', 0) - resource.get('inventory_count', 0)} unité(s))", "blue", "info")
            self.pickup_specific_resource(resource)
            time.sleep(3)
        self.goto_auction_house()

    def update_price_loop(self):
        sale_enabled_resources = [r for r in self.resources if r.get("sale_enabled", False)]
        if sale_enabled_resources and not hasattr(self, "initial_pickup_done"):
            self.pickup_resources()
            self.initial_pickup_done = True
        if sale_enabled_resources:
            self.goto_auction_house()

        while True:
            if hasattr(self, "bank_trip_due") and self.bank_trip_due:
                self.log("Début du cycle : retour groupé en banque pour récupérer les ressources manquantes.", "blue", "info")
                self.pickup_missing_resources(self.missing_resources)
                self.bank_trip_due = False
                self.log(f"{self.interval} secondes avant le prochain cycle...", "yellow", "info")
                time.sleep(self.interval)
                continue

            if not self.running or self.paused:
                time.sleep(1)
                continue

            if sale_enabled_resources:
                self.sell_resources()

            for resource in self.resources:
                if not resource.get("sale_enabled", False) and resource.get("pickup", 0) <= 0:
                    resource["pickup"] = 100
                self.log(f"Actualisation de {resource['name']}", "cyan", "info")
                self.select_resource(resource["name"], resource.get("lot", 100), actualisation_only=True)

            self.log(f"{self.interval} secondes avant le prochain cycle...", "yellow", "info")
            time.sleep(self.interval)

            missing = []
            for resource in self.resources:
                if resource.get("sale_enabled", False):
                    current = resource.get("inventory_count", 0)
                    bank_count = resource.get("bank_count", 0)
                    lot_size = resource.get("lot", 100)
                    if current == 0 and bank_count >= lot_size:
                        missing.append(resource)
            if missing:
                self.log("Inventaire insuffisant détecté pour certaines ressources disposant d'un stock suffisant en banque. Retour en banque programmé pour le prochain cycle.", "blue", "info")
                self.missing_resources = missing
                self.bank_trip_due = True
            else:
                self.log("Aucune ressource disponible en banque à récupérer. Actualisation uniquement.", "blue", "info")

            sale_enabled_resources = [r for r in self.resources if r.get("sale_enabled", False)]

    def start_loop(self):
        if not self.running:
            self.running = True
            self.loop_thread = threading.Thread(target=self.update_price_loop, daemon=True)
            self.loop_thread.start()
            self.log("Bot démarré avec succès !", "green", "success")

    def stop(self):
        if self.running:
            self.paused = not self.paused
            if self.paused:
                self.log("Bot mis en pause.", "red", "warning")
            else:
                self.log("Bot repris.", "green", "success")

    def send_private_notification(self, message):
        if not self.discord_id:
            print("Discord ID non configuré. Impossible d'envoyer la notification privée.")
            return
        try:
            discord_id_int = int(self.discord_id)
        except ValueError:
            print("Discord ID invalide. Il doit être un nombre.")
            return
        try:
            coro = send_dm(discord_id_int, message)
            future = asyncio.run_coroutine_threadsafe(coro, discord_client.loop)
            future.result(timeout=10)
        except Exception as e:
            print(f"Erreur lors de l'envoi du DM : {e}")

# -----------------------------
# Interface Graphique (CustomTkDofusPriceBotGUI)
# -----------------------------
class CustomTkDofusPriceBotGUI:
    def __init__(self, bot, config):
        self.bot = bot
        self.config = config
        
        # Assurer que les clés de config existent
        if "price_drop" not in self.config:
            self.config["price_drop"] = 0
        if "use_security" not in self.config:
            self.config["use_security"] = False
        if "security_percent" not in self.config:
            self.config["security_percent"] = 10

        print("Config chargée au démarrage :", self.config)
        
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")

        self.root = ctk.CTk()
        self.root.title("RitsujiHDV - Assistant HDV 1.5")
        self.root.geometry("700x750")
        self.root.resizable(False, False)

        self.top_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        self.top_frame.pack(pady=(15,5), padx=20, fill="x")
        self.config_toggle_button = ctk.CTkButton(self.top_frame, text="Configuration ▼", command=self.toggle_config_panel, width=150)
        self.config_toggle_button.pack()

        self.config_frame = ctk.CTkFrame(self.top_frame, corner_radius=10, fg_color="gray25")
        self.options_frame = ctk.CTkFrame(self.config_frame, fg_color="transparent")
        self.options_frame.pack(side="top", fill="x", pady=(5,5))
        self.update_frame = ctk.CTkFrame(self.config_frame, fg_color="transparent")
        self.update_frame.pack(side="bottom", fill="x", pady=(10,5))

        # Intervalle
        self.interval_label = ctk.CTkLabel(self.options_frame, text="Intervalle (sec):")
        self.interval_label.pack(pady=(5,2))
        self.interval_entry = ctk.CTkEntry(self.options_frame, placeholder_text="Entrez l'intervalle")
        self.interval_entry.pack(pady=(0,5))
        self.interval_entry.insert(0, str(self.bot.interval))
        
        # Checkbox "Activer la baisse de prix"
        self.drop_checkbox = ctk.CTkCheckBox(self.options_frame, text="Activer la baisse de prix", command=self.update_drop)
        self.drop_checkbox.pack(pady=(5,2))
        if self.config.get("price_drop", 0) > 0:
            self.drop_checkbox.select()
        else:
            self.drop_checkbox.deselect()
        # Frame pour la baisse de prix
        self.drop_frame = ctk.CTkFrame(self.options_frame, fg_color="transparent")
        self.drop_inner_frame = ctk.CTkFrame(self.drop_frame, fg_color="transparent")
        self.drop_inner_frame.pack(anchor="center")
        self.price_drop_label = ctk.CTkLabel(self.drop_inner_frame, text="Baisse de prix (kamas) :")
        self.price_drop_label.pack(side="left", padx=5)
        self.price_drop_entry = ctk.CTkEntry(self.drop_inner_frame, placeholder_text="Entrez la baisse en kamas")
        self.price_drop_entry.pack(side="left", padx=5)
        self.price_drop_entry.insert(0, str(self.config.get("price_drop", 0)))
        self.update_drop()  # Appel pour afficher le frame si nécessaire

        # Checkbox "Activer la sécurité"
        self.security_checkbox = ctk.CTkCheckBox(self.options_frame, text="Activer la sécurité", command=self.update_security)
        self.security_checkbox.pack(pady=(5,2))
        if self.config.get("use_security", False):
            self.security_checkbox.select()
        else:
            self.security_checkbox.deselect()
        # Frame pour la sécurité
        self.security_frame = ctk.CTkFrame(self.options_frame, fg_color="transparent")
        self.security_inner_frame = ctk.CTkFrame(self.security_frame, fg_color="transparent")
        self.security_inner_frame.pack(anchor="center")
        self.security_percent_label = ctk.CTkLabel(self.security_inner_frame, text="Pourcentage de sécurité (%) :")
        self.security_percent_label.pack(side="left", padx=5)
        self.security_percent_entry = ctk.CTkEntry(self.security_inner_frame, placeholder_text="Entrez le pourcentage")
        self.security_percent_entry.pack(side="left", padx=5)
        self.security_percent_entry.insert(0, str(self.config.get("security_percent", 10)))
        self.update_security()  # Appel pour afficher le frame si nécessaire
        
        # Discord
        self.discord_frame = ctk.CTkFrame(self.options_frame, fg_color="transparent")
        self.discord_label = ctk.CTkLabel(self.discord_frame, text="Discord ID :")
        self.discord_label.pack(pady=(5,2))
        self.discord_entry = ctk.CTkEntry(self.discord_frame, placeholder_text="Entrez votre Discord ID")
        self.discord_entry.pack(pady=(0,5))
        if "discord_id" in self.config:
            self.discord_entry.insert(0, self.config["discord_id"])
            self.bot.discord_id = self.config["discord_id"]
        self.discord_frame.pack(pady=(5,2), fill="x")
        
        self.update_interval_button = ctk.CTkButton(self.update_frame, text="Mettre à jour la config", command=self.update_interval)
        self.update_interval_button.pack(fill="x", pady=(0,5))

        self.config_visible = False

        # Panneau Ressources
        self.resources_frame = ctk.CTkFrame(self.root, corner_radius=10, width=600, height=300)
        self.resources_frame.pack(pady=15, padx=20, fill="x")
        self.resources_frame.pack_propagate(False)
        self.resources_label = ctk.CTkLabel(self.resources_frame, text="Ressources", font=ctk.CTkFont(size=20, weight="bold"))
        self.resources_label.pack(pady=10)
        self.resource_input_frame = ctk.CTkFrame(self.resources_frame, fg_color="transparent")
        self.resource_input_frame.pack(pady=5, padx=10, fill="x")
        self.resource_entry = ctk.CTkEntry(self.resource_input_frame, placeholder_text="Nom de la ressource")
        self.resource_entry.pack(side="left", expand=True, fill="x", padx=(0,5))
        self.resource_entry.bind("<Return>", lambda event: self.add_resource())
        self.add_resource_button = ctk.CTkButton(self.resource_input_frame, text="+", command=self.add_resource, width=40)
        self.add_resource_button.pack(side="right")
        self.resources_scrollable_frame = ctk.CTkScrollableFrame(self.resources_frame, height=150)
        self.resources_scrollable_frame.pack(pady=5, padx=10, fill="both", expand=True)
        
        if "resources" in self.config:
            self.bot.resources = []
            for resource in self.config["resources"]:
                resource["inventory_count"] = 0
                resource["bank_count"] = 0
                self.bot.resources.append(resource)
                self.display_resource(resource)

        # Panneau de contrôle
        self.control_frame = ctk.CTkFrame(self.root, corner_radius=10)
        self.control_frame.pack(pady=15, padx=20, fill="x")
        self.control_button = ctk.CTkButton(self.control_frame, text="Démarrer", command=self.toggle_bot, width=150)
        self.control_button.pack(pady=10)
        self.status_label = ctk.CTkLabel(self.control_frame, text="Statut: Arrêté", text_color="red")
        self.status_label.pack(pady=(0,10))
        self.update_resources_panel()

        self.logs_frame = ctk.CTkFrame(self.root, corner_radius=10)
        self.logs_frame.pack(pady=15, padx=20, fill="both", expand=True)
        self.logs_label = ctk.CTkLabel(self.logs_frame, text="Journaux", font=ctk.CTkFont(size=20, weight="bold"))
        self.logs_label.pack(pady=10)
        self.logs_textbox = tk.Text(self.logs_frame, height=300, bg="#1e1e1e", fg="white", font=("Helvetica", 12))
        self.logs_textbox.pack(pady=5, padx=10, fill="both", expand=True)
        self.logs_textbox.configure(state="disabled")

        self.original_log = self.bot.log
        def gui_log(message, color="white", prefix=""):
            self.original_log(message, color, prefix)
            prefix_map = {"info": "🔍 ", "success": "✅ ", "warning": "⚠️ ", "error": "🚨 "}
            display_prefix = prefix_map.get(prefix, "")
            self.append_log(display_prefix, message, color)
        self.bot.log = gui_log

        keyboard.add_hotkey("F6", self.toggle_bot)
        keyboard.add_hotkey("F8", self.toggle_bot)
        keyboard.add_hotkey("F12", self.panic)

    def display_resource(self, resource_dict):
        resource_name = resource_dict["name"]
        resource_frame = ctk.CTkFrame(self.resources_scrollable_frame, fg_color="transparent")
        resource_frame.pack(fill="x", pady=5, padx=5)
        top_frame = ctk.CTkFrame(resource_frame, fg_color="transparent")
        top_frame.pack(fill="x")
        
        resource_label = ctk.CTkLabel(top_frame, text=resource_name, anchor="w", width=150)
        resource_label.grid(row=0, column=0, sticky="w", padx=(5,5))
        
        def on_lot_change(value):
            if value == "Lot de 100":
                resource_dict["lot"] = 100
            elif value == "Lot de 10":
                resource_dict["lot"] = 10
            elif value == "Lot de 1":
                resource_dict["lot"] = 1
            self.append_log("", f"Lot mis à jour pour {resource_name} : {value}", "blue")
            self.config["resources"] = filter_resource_data(self.bot.resources)
            save_config(self.config)
        
        lot_combobox = ctk.CTkComboBox(top_frame, 
                                       values=["Lot de 100", "Lot de 10", "Lot de 1"],
                                       command=on_lot_change, 
                                       width=120,
                                       state="readonly")
        lot_combobox.set(f"Lot de {resource_dict.get('lot', 100)}")
        lot_combobox.grid(row=0, column=1, sticky="e", padx=(5,0))
        
        sale_var = tk.BooleanVar(value=resource_dict.get("sale_enabled", False))
        def on_sale_toggle():
            resource_dict["sale_enabled"] = sale_var.get()
            if sale_var.get():
                if "pickup" not in resource_dict or resource_dict["pickup"] <= 0:
                    resource_dict["pickup"] = 1
                if "desired" not in resource_dict or resource_dict["desired"] <= 0:
                    resource_dict["desired"] = 15
                self.append_log("", f"Vente activée pour {resource_name} !", "blue")
            else:
                if resource_dict.get("pickup", 0) <= 0:
                    resource_dict["pickup"] = 100
                self.append_log("", f"Vente désactivée pour {resource_name}, passage en mode actualisation (pickup=100).", "blue")
            self.config["resources"] = filter_resource_data(self.bot.resources)
            save_config(self.config)
            resource_frame.destroy()
            self.display_resource(resource_dict)
        sale_checkbox = ctk.CTkCheckBox(top_frame, text="Activer vente", variable=sale_var, command=on_sale_toggle)
        sale_checkbox.grid(row=0, column=2, padx=(5,5))
        
        delete_button = ctk.CTkButton(top_frame, text="X", fg_color="red", text_color="white",
                                       command=lambda: self.delete_resource(resource_dict, resource_frame), width=30)
        delete_button.grid(row=0, column=3, sticky="e", padx=(0,5))
        
        if resource_dict.get("sale_enabled", False):
            bottom_frame = ctk.CTkFrame(resource_frame, fg_color="transparent")
            bottom_frame.pack(fill="x", pady=(5,0))
            
            pickup_label = ctk.CTkLabel(bottom_frame, text="Quantité à pickup :", width=140)
            pickup_label.grid(row=0, column=0, sticky="w", padx=(5,5))
            pickup_entry = ctk.CTkEntry(bottom_frame, width=80, placeholder_text="Quantité")
            pickup_entry.grid(row=0, column=1, padx=(5,5))
            pickup_entry.insert(0, str(resource_dict.get("pickup", 1)))
            def update_pickup(event):
                try:
                    resource_dict["pickup"] = int(pickup_entry.get())
                    self.append_log("", f"Quantité pickup mise à jour pour {resource_name} : {resource_dict['pickup']}", "blue")
                    self.config["resources"] = filter_resource_data(self.bot.resources)
                    save_config(self.config)
                except ValueError:
                    self.append_log("", "Quantité invalide.", "red")
            pickup_entry.bind("<FocusOut>", update_pickup)
            
            desired_label = ctk.CTkLabel(bottom_frame, text="Lot(s) en vente(s) :", width=140)
            desired_label.grid(row=0, column=2, sticky="w", padx=(5,5))
            desired_entry = ctk.CTkEntry(bottom_frame, width=80, placeholder_text="Lots")
            desired_entry.grid(row=0, column=3, padx=(5,5))
            desired_entry.insert(0, str(resource_dict.get("desired", 15)))
            def update_desired(event):
                try:
                    resource_dict["desired"] = int(desired_entry.get())
                    self.append_log("", f"Lots souhaités mis à jour pour {resource_name} : {resource_dict['desired']}", "blue")
                    self.config["resources"] = filter_resource_data(self.bot.resources)
                    save_config(self.config)
                except ValueError:
                    self.append_log("", "Nombre de lots invalide.", "red")
            desired_entry.bind("<FocusOut>", update_desired)
            
            bank_label = ctk.CTkLabel(bottom_frame, text="Quantité en banque :", width=140)
            bank_label.grid(row=1, column=0, sticky="w", padx=(5,5))
            bank_entry = ctk.CTkEntry(bottom_frame, width=80, placeholder_text="Banque")
            bank_entry.grid(row=1, column=1, padx=(5,5))
            bank_entry.insert(0, str(resource_dict.get("bank_count", 0)))
            def update_bank(event):
                try:
                    resource_dict["bank_count"] = int(bank_entry.get())
                    self.append_log("", f"Quantité en banque mise à jour pour {resource_name} : {resource_dict['bank_count']}", "blue")
                except ValueError:
                    self.append_log("", "Quantité en banque invalide.", "red")
            bank_entry.bind("<FocusOut>", update_bank)

    def update_drop(self):
        if self.drop_checkbox.get():
            if not self.drop_frame.winfo_ismapped():
                self.drop_frame.pack(pady=(0,5), fill="x")
        else:
            self.drop_frame.pack_forget()
        try:
            new_drop = int(self.price_drop_entry.get())
            self.bot.price_drop = new_drop
        except ValueError:
            self.bot.price_drop = 0
        self.config["price_drop"] = self.bot.price_drop
        save_config(self.config)
        print("Config après update_drop :", self.config)

    def update_security(self):
        if self.security_checkbox.get():
            if not self.security_frame.winfo_ismapped():
                self.security_frame.pack(pady=(0,5), fill="x")
        else:
            self.security_frame.pack_forget()
        self.config["use_security"] = self.security_checkbox.get()
        try:
            new_sec = int(self.security_percent_entry.get())
            self.bot.security_percent = new_sec
        except ValueError:
            self.bot.security_percent = 0
        self.config["security_percent"] = self.bot.security_percent
        save_config(self.config)
        print("Config après update_security :", self.config)

    def update_interval(self):
        try:
            new_interval = int(self.interval_entry.get())
            self.bot.interval = new_interval
            self.append_log("", f"Intervalle mis à jour à {new_interval} secondes.", "blue")
        except ValueError:
            self.append_log("", "Intervalle invalide.", "red")
        if self.drop_checkbox.get():
            try:
                new_drop = int(self.price_drop_entry.get())
                self.bot.price_drop = new_drop
                self.append_log("", f"Baisse de prix mise à jour à {new_drop} kamas.", "blue")
            except ValueError:
                self.append_log("", "Baisse de prix invalide.", "red")
        else:
            self.bot.price_drop = 0

        self.bot.use_security = self.security_checkbox.get()
        if self.bot.use_security:
            try:
                new_sec = int(self.security_percent_entry.get())
                self.bot.security_percent = new_sec
                self.append_log("", f"Sécurité activée à {new_sec}%.", "blue")
            except ValueError:
                self.append_log("", "Pourcentage de sécurité invalide.", "red")
        else:
            self.bot.security_percent = 0
            self.append_log("", "Sécurité désactivée.", "blue")
        discord_id = self.discord_entry.get().strip()
        self.bot.discord_id = discord_id
        self.config["discord_id"] = discord_id

        self.config["price_drop"] = self.bot.price_drop
        self.config["use_security"] = self.bot.use_security
        self.config["security_percent"] = self.bot.security_percent
        self.config["resources"] = filter_resource_data(self.bot.resources)
        try:
            save_config(self.config)
            print("Configuration sauvegardée :", self.config)
        except Exception as e:
            self.append_log("", f"Erreur lors de la sauvegarde de la config : {e}", "red")

    def toggle_config_panel(self):
        if self.config_visible:
            self.config_frame.pack_forget()
            self.config_toggle_button.configure(text="Configuration ▼")
        else:
            self.config_frame.pack(pady=5, fill="x")
            self.config_toggle_button.configure(text="Configuration ▲")
        self.config_visible = not self.config_visible

    def update_resources_panel(self):
        if len(self.resources_scrollable_frame.winfo_children()) == 0:
            self.control_button.configure(state="disabled")
        else:
            self.control_button.configure(state="normal")

    def add_resource(self):
        resource_name = self.resource_entry.get().strip()
        if resource_name:
            resource_dict = {"name": resource_name, "lot": 100, "pickup": 0, "desired": 15, "sale_enabled": False, "inventory_count": 0, "bank_count": 0}
            self.bot.resources.append(resource_dict)
            self.display_resource(resource_dict)
            self.resource_entry.delete(0, "end")
            self.update_resources_panel()
            self.append_log("", f"Ressource ajoutée : {resource_name}", "green")
            self.config["resources"] = filter_resource_data(self.bot.resources)
            save_config(self.config)

    def delete_resource(self, resource_dict, resource_frame):
        if resource_dict in self.bot.resources:
            self.bot.resources.remove(resource_dict)
        resource_frame.destroy()
        self.update_resources_panel()
        self.append_log("", f"Ressource supprimée : {resource_dict['name']}", "red")
        self.config["resources"] = filter_resource_data(self.bot.resources)
        save_config(self.config)

    def toggle_bot(self):
        if not self.bot.running:
            try:
                self.bot.interval = int(self.interval_entry.get())
            except ValueError:
                self.bot.interval = 20
            self.bot.running = True
            self.bot.paused = False
            self.bot.loop_thread = threading.Thread(target=self.bot.update_price_loop, daemon=True)
            self.bot.loop_thread.start()
            self.control_button.configure(text="Pause")
            self.status_label.configure(text="Statut: En cours", text_color="green")
        else:
            if not self.bot.paused:
                self.bot.paused = True
                self.control_button.configure(text="Reprendre")
                self.status_label.configure(text="Statut: En pause", text_color="orange")
            else:
                self.bot.paused = False
                self.control_button.configure(text="Pause")
                self.status_label.configure(text="Statut: En cours", text_color="green")

    def panic(self):
        """Panic key: arrête immédiatement le bot sans fermer l'application."""
        self.bot.running = False
        self.bot.paused = True
        self.append_log("", "Panic key activée! Bot stoppé.", "red")

    def append_log(self, prefix, message, color="white"):
        timestamp = time.strftime("%H:%M:%S")
        log_line = f"[{timestamp}] {prefix}{message}\n"
        self.logs_textbox.configure(state="normal")
        start_index = self.logs_textbox.index("end")
        self.logs_textbox.insert("end", log_line)
        end_index = self.logs_textbox.index("end-1c")
        self.logs_textbox.tag_add(color, start_index, end_index)
        self.logs_textbox.tag_configure(color, foreground=color, font=("Helvetica", 12, "bold"))
        self.logs_textbox.see("end")
        self.logs_textbox.configure(state="disabled")

    def run(self):
        self.root.mainloop()

# -----------------------------
# Partie Authentification KeyAuth et modules divers
# -----------------------------
try:
    if os.name == 'nt':
        import win32security
    import requests
    from Crypto.Cipher import AES
    from Crypto.Hash import SHA256
    from Crypto.Util.Padding import pad, unpad
except ModuleNotFoundError:
    print("❌ Exception lors de l'importation des modules")
    if os.path.isfile("requirements.txt"):
        os.system("pip install -r requirements.txt")
    else:
        os.system("pip install pywin32 pycryptodome requests")
    time.sleep(1.5)
    os._exit(1)

try:
    s = requests.Session()
    s.get('https://google.com')
except requests.exceptions.RequestException as e:
    print(f"⏰ Erreur de connexion : {e}")
    time.sleep(3)
    os._exit(1)

class api:
    name = ownerid = secret = version = hash_to_check = ""

    def __init__(self, name, ownerid, secret, version, hash_to_check):
        self.name = name
        self.ownerid = ownerid
        self.secret = secret
        self.version = version
        self.hash_to_check = hash_to_check
        self.init()

    sessionid = enckey = ""
    initialized = False

    def init(self):
        if self.sessionid != "":
            print("🚀 Initialisation déjà effectuée !")
            time.sleep(2)
            os._exit(1)
        init_iv = SHA256.new(str(uuid4())[:8].encode()).hexdigest()
        self.enckey = SHA256.new(str(uuid4())[:8].encode()).hexdigest()
        post_data = {
            "type": binascii.hexlify(("init").encode()),
            "ver": encryption.encrypt(self.version, self.secret, init_iv),
            "hash": self.hash_to_check,
            "enckey": encryption.encrypt(self.enckey, self.secret, init_iv),
            "name": binascii.hexlify(self.name.encode()),
            "ownerid": binascii.hexlify(self.ownerid.encode()),
            "init_iv": init_iv
        }
        response = self.__do_request(post_data)
        if response == "KeyAuth_Invalid":
            print("❌ L'application n'existe pas !")
            os._exit(1)
        response = encryption.decrypt(response, self.secret, init_iv)
        json_resp = jsond.loads(response)
        
        if json_resp["message"] == "invalidver":
            if json_resp["download"] != "":
                print("🚀 Nouvelle version disponible !")
                import tkinter as tk
                from tkinter import messagebox
                root = tk.Tk()
                root.withdraw()
                confirmation = messagebox.askokcancel(
                    "Ritsuji - Assistant HDV",
                    "Une nouvelle version est disponible ! \nCliquez sur OK pour lancer la mise à jour.\nMerci de ne rien toucher pendant le processus."
                )
                root.destroy()
                if not confirmation:
                    print("Mise à jour annulée par l'utilisateur.")
                    os._exit(0)
                print("Téléchargement en cours...")
                download_link = json_resp["download"]
                try:
                    r = requests.get(download_link, stream=True, timeout=30)
                    r.raise_for_status()
                    base_path = os.getcwd()
                    new_loader_temp = os.path.join(base_path, "Ritsuji_new.exe")
                    with open(new_loader_temp, "wb") as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                    print("Téléchargement terminé.")
                    time.sleep(2)
                    current_loader = os.path.join(base_path, "Ritsuji.exe")
                    old_loader = os.path.join(base_path, "Ritsuji_old.exe")
                    if os.path.exists(current_loader):
                        if os.path.exists(old_loader):
                            os.remove(old_loader)
                            print(f"Ancien loader {old_loader} supprimé.")
                        time.sleep(1)
                        os.rename(current_loader, old_loader)
                        print(f"Renommage de {current_loader} en {old_loader}.")
                    else:
                        print(f"{current_loader} n'existe pas. Pas de renommage.")
                    time.sleep(2)
                    os.rename(new_loader_temp, current_loader)
                    print(f"Le nouveau loader a été renommé en {current_loader}.")
                    time.sleep(2)
                    batch_script = os.path.join(base_path, "update_cleanup.bat")
                    with open(batch_script, "w") as bat:
                        bat.write(f'''@echo off
timeout /t 5 >nul
if exist "{old_loader}" (
    del /f /q "{old_loader}"
)
del /f /q "{batch_script}"
exit
    ''')
                    print("Script batch de nettoyage créé.")
                    time.sleep(1)
                    subprocess.Popen(["cmd", "/c", batch_script], creationflags=subprocess.CREATE_NO_WINDOW)
                    print("Script batch de nettoyage lancé.")
                    time.sleep(1)
                    subprocess.Popen([current_loader], shell=True)
                    print("Nouveau loader lancé.")
                    time.sleep(1)
                    os._exit(0)
                except Exception as e:
                    print(f"❌ Erreur lors de la mise à jour automatique : {e}")
                    os._exit(1)
            else:
                print("❌ Version invalide, contactez le propriétaire pour obtenir la dernière version !")
                os._exit(1)
        if not json_resp["success"]:
            print(f"❌ Erreur : {json_resp['message']}")
            os._exit(1)
        self.sessionid = json_resp["sessionid"]
        self.initialized = True
        self.__load_app_data(json_resp["appinfo"])

    def license(self, key, hwid=None):
        self.checkinit()
        if hwid is None:
            hwid = others.get_hwid()
        init_iv = SHA256.new(str(uuid4())[:8].encode()).hexdigest()
        post_data = {
            "type": binascii.hexlify(("license").encode()),
            "key": encryption.encrypt(key, self.enckey, init_iv),
            "hwid": encryption.encrypt(hwid, self.enckey, init_iv),
            "sessionid": binascii.hexlify(self.sessionid.encode()),
            "name": binascii.hexlify(self.name.encode()),
            "ownerid": binascii.hexlify(self.ownerid.encode()),
            "init_iv": init_iv
        }
        response = self.__do_request(post_data)
        response = encryption.decrypt(response, self.enckey, init_iv)
        json_resp = jsond.loads(response)
        if json_resp["success"]:
            print("🎉 Licence validée avec succès !")
            self.__load_user_data(json_resp["info"])
        else:
            raise ValueError("Clé de licence invalide")

    def login(self, user, password, hwid=None):
        self.checkinit()
        if hwid is None:
            hwid = others.get_hwid()
        init_iv = SHA256.new(str(uuid4())[:8].encode()).hexdigest()
        post_data = {
            "type": binascii.hexlify(("login").encode()),
            "username": encryption.encrypt(user, self.enckey, init_iv),
            "pass": encryption.encrypt(password, self.enckey, init_iv),
            "hwid": encryption.encrypt(hwid, self.enckey, init_iv),
            "sessionid": binascii.hexlify(self.sessionid.encode()),
            "name": binascii.hexlify(self.name.encode()),
            "ownerid": binascii.hexlify(self.ownerid.encode()),
            "init_iv": init_iv
        }
        response = self.__do_request(post_data)
        response = encryption.decrypt(response, self.enckey, init_iv)
        json_resp = jsond.loads(response)
        if json_resp["success"]:
            self.__load_user_data(json_resp["info"])
            print("🎉 Connexion réussie !")
        else:
            print(f"❌ {json_resp['message']}")
            os._exit(1)

    def checkinit(self):
        if not self.initialized:
            print("❌ Veuillez initialiser l'application avant d'utiliser les fonctions.")
            time.sleep(2)
            os._exit(1)

    def __do_request(self, post_data):
        try:
            rq_out = s.post("https://keyauth.win/api/1.0/", data=post_data, timeout=30)
            return rq_out.text
        except requests.exceptions.Timeout:
            print("⏰ La requête a expiré.")

    def __load_app_data(self, data):
        self.app_data = data

    def __load_user_data(self, data):
        self.user_data = data

class others:
    @staticmethod
    def get_hwid():
        if platform.system() == "Linux":
            with open("/etc/machine-id") as f:
                hwid = f.read()
                return hwid
        elif platform.system() == 'Windows':
            import win32security
            winuser = os.getlogin()
            sid = win32security.LookupAccountName(None, winuser)[0]
            hwid = win32security.ConvertSidToStringSid(sid)
            return hwid
        elif platform.system() == 'Darwin':
            output = subprocess.Popen("ioreg -l | grep IOPlatformSerialNumber", stdout=subprocess.PIPE, shell=True).communicate()[0]
            serial = output.decode().split('=', 1)[1].replace(' ', '')
            hwid = serial[1:-2]
            return hwid

class encryption:
    @staticmethod
    def encrypt_string(plain_text, key, iv):
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import pad
        plain_text = pad(plain_text, 16)
        aes_instance = AES.new(key, AES.MODE_CBC, iv)
        raw_out = aes_instance.encrypt(plain_text)
        return binascii.hexlify(raw_out)

    @staticmethod
    def decrypt_string(cipher_text, key, iv):
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import unpad
        cipher_text = binascii.unhexlify(cipher_text)
        aes_instance = AES.new(key, AES.MODE_CBC, iv)
        cipher_text = aes_instance.decrypt(cipher_text)
        return unpad(cipher_text, 16)

    @staticmethod
    def encrypt(message, enc_key, iv):
        from Crypto.Hash import SHA256
        try:
            _key = SHA256.new(enc_key.encode()).hexdigest()[:32]
            _iv = SHA256.new(iv.encode()).hexdigest()[:16]
            return encryption.encrypt_string(message.encode(), _key.encode(), _iv.encode()).decode()
        except Exception as e:
            print("❌ Informations d'application invalides.")
            os._exit(1)

    @staticmethod
    def decrypt(message, enc_key, iv):
        from Crypto.Hash import SHA256
        try:
            _key = SHA256.new(enc_key.encode()).hexdigest()[:32]
            _iv = SHA256.new(iv.encode()).hexdigest()[:16]
            return encryption.decrypt_string(message.encode(), _key.encode(), _iv.encode()).decode()
        except Exception as e:
            print("❌ Informations d'application invalides.")
            os._exit(1)

def getchecksum():
    md5_hash = hashlib.md5()
    with open(sys.argv[0], "rb") as file:
        md5_hash.update(file.read())
    digest = md5_hash.hexdigest()
    return digest

global_config = load_config()
if "license" not in global_config:
    global_config["license"] = ""
if not isinstance(global_config.get("resources"), list):
    global_config["resources"] = []

keyauthapp = api(
    name="Kuroooo6754's Application",
    ownerid="pVoIQ1e4JL",
    secret="2b0eae83c08d5a47c1f8891cbeda33ffaffa02944f63baadba83fc7a0991cb53",
    version="1.5",
    hash_to_check=getchecksum()
)

class LoginWindow(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Authentification")
        self.geometry("400x200")
        self.resizable(False, False)
        
        self.label = ctk.CTkLabel(self, text="Entrez votre clé de licence :", font=ctk.CTkFont(size=16, weight="bold"))
        self.label.pack(pady=20)
        
        self.license_entry = ctk.CTkEntry(self, placeholder_text="Clé de licence")
        self.license_entry.pack(pady=10, padx=20, fill="x")
        
        if global_config["license"]:
            self.license_entry.insert(0, global_config["license"])
        
        self.login_button = ctk.CTkButton(self, text="Se connecter", command=self.perform_login)
        self.login_button.pack(pady=10)
    
    def perform_login(self):
        license_key = self.license_entry.get().strip()
        if not license_key:
            messagebox.showerror("Erreur", "Veuillez entrer une clé de licence.")
            return
        try:
            keyauthapp.license(license_key)
        except ValueError as e:
            messagebox.showerror("Erreur", str(e))
            return
        global_config["license"] = license_key
        save_config(global_config)
        self.destroy()
        bot = DofusPriceBot()
        gui = CustomTkDofusPriceBotGUI(bot, global_config)
        gui.run()

if __name__ == "__main__":
    login_window = LoginWindow()
    login_window.mainloop()
