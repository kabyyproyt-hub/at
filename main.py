import os
import re
import json
import uuid
import random
import calendar
import itertools
import asyncio
import aiohttp
import discord
from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv
load_dotenv()

# ================================================================
#                        CONFIGURATION
# ================================================================
PREFIX = "+"

STAFF_ROLE_NAME = "STAFF"          # Nom exact du rôle staff sur ton serveur (utilisé pour tickets, guildes, etc.)
STATS_CATEGORY_NAME = "🧽 SERVEUR STATS"
STATS_UPDATE_INTERVAL_MINUTES = 10      # Discord limite les renommages de salons (~2 / 10 min)
CONFIG_FILE = "config.json"             # Stockage persistant des rôles autorisés à valider
DEV_GUILD_ID = 1537139988448153640      # ID de ton serveur, pour une synchro instantanée des slash commands

# ---- Hiérarchie de modération (Helper < Mod < Gérant) ----
# Chaque rôle supérieur hérite automatiquement des permissions du rôle inférieur.
HELPER_ROLE_NAME = "・Modérateur Test"
MOD_ROLE_NAME = "⚒️┆Gestion staff"
GERANT_ROLE_NAME = "💎┆Gérant Staff"
 
# ---- Élu de la semaine ----
ELU_ROLE_NAME = "👑 Élu de la semaine"
ELU_GIF_URL = "https://media1.tenor.com/m/9BEFbzse_iUAAAAC/hunter-x-hunter-vacuum.gif"
PARIS_TZ = ZoneInfo("Europe/Paris")

# ---- Tickets ----
TICKETS_CATEGORY_NAME = "『🕵️』Ticket Ouvert"     # Catégorie par défaut où sont créés les salons de tickets

# ---- TikTok ----
TIKTOK_USERNAME = "7vkp2"                # Compte TikTok suivi (https://www.tiktok.com/@7vkp2)
TIKTOK_CHECK_INTERVAL_MINUTES = 10       # Fréquence de vérification des nouvelles vidéos
# ================================================================
 
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.presences = True
 
bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)
 
 
def is_staff(member: discord.Member) -> bool:
    """Vérifie si un membre est staff (rôle 'Staff' ou permission administrateur)."""
    if member.guild_permissions.administrator:
        return True
    return any(role.name == STAFF_ROLE_NAME for role in member.roles)


def is_gerant(member: discord.Member) -> bool:
    """Niveau le plus haut : ban, mute, lock/unlock, retrait de warns, +add role.
    Inclut aussi le rôle STAFF_ROLE_NAME existant, pour ne rien casser des
    fonctionnalités déjà réservées au staff (tickets, guildes, etc.)."""
    if member.guild_permissions.administrator:
        return True
    if any(role.name == GERANT_ROLE_NAME for role in member.roles):
        return True
    return is_staff(member)


def is_mod(member: discord.Member) -> bool:
    """Niveau intermédiaire : mute, warn, clear. Ne peut PAS retirer de warns."""
    if is_gerant(member):
        return True
    return any(role.name == MOD_ROLE_NAME for role in member.roles)


def is_helper(member: discord.Member) -> bool:
    """Niveau le plus bas : peut seulement warn. Ne peut PAS retirer de warns."""
    if is_mod(member):
        return True
    return any(role.name == HELPER_ROLE_NAME for role in member.roles)
 
 
# ---------------- Config persistante (rôles de validation) ----------------
 
def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}
 
 
def save_config(data: dict) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
 
 
config = load_config()
 
 
def get_validator_roles(guild_id: int, categorie: str) -> list:
    return config.get(str(guild_id), {}).get(categorie, [])
 
 
def add_validator_role(guild_id: int, categorie: str, role_id: int) -> None:
    guild_conf = config.setdefault(str(guild_id), {})
    roles = guild_conf.setdefault(categorie, [])
    if role_id not in roles:
        roles.append(role_id)
    save_config(config)

 
# ================================================================
#                          +cmds
# ================================================================
 
NORMAL_COMMANDS = [
("⚙️ Utilitaires", [
        ("`+cmds`", "Affiche la liste des commandes disponibles."),
        ("`+ping`", "Affiche la latence du bot."),
        ("`+invite-stats`", "Affiche combien de membres tu as invités sur le serveur."),
    ]),
    ("🎥 Concept", [
        ("`+concept list`", "Affiche la liste des notés du Concept."),
    ]),
    ("📋 Report", [
        ("`/report envoyer [@membre] [raison]`", "Signale discrètement un membre au staff."),
    ]),
    ("🐾 Animaux & Compagnons", [
        ("`/pet inventory`", "Affiche ton inventaire d'animaux capturés."),
        ("`/pet trade [@membre]`", "Propose un échange d'animal avec un autre membre."),
    ]),
    ("📕 Guildes", [
        ("`/guilde create`", "Crée une guilde et ouvre le formulaire de création."),
        ("`+block guild [id]`", "Bloque l'XP d'une guilde adverse 30 min (1200$, 1x/jour, fondateur)."),
        ("`+xp fast`", "Booste l'XP de ta propre guilde pendant 5 min (1x/jour, fondateur)."),
    ]),
]
 
STAFF_COMMANDS = [
    ("+absences", "Ouvre un formulaire pour déclarer une absence."),
    ("+role-react setup", "Crée un message à réactions qui donne des rôles."),
    ("/ticketsetup", "Crée un panneau de tickets personnalisable (staff)."),
    ("/admin panel", "Ouvre le panneau d'administration : boost de chance x10, spawn x10 (staff)."),
    ("/soutiens", "Configure et affiche le panneau des soutiens du serveur (staff)."),
    ("+concept note @membre", "Notez une personne dans la listes des concepts."),
    ("+concept list reset", "Réinitialise la liste Concept."),
    ("/report config [salon]", "Définit le salon privé où arrivent les signalements (staff)."),
    ("/report historique [@membre]", "Affiche les signalements reçus contre un membre (staff)."),
    ("/guilde verif", "Affiche les guildes en attente de vérification."),
    ("/guilde delete [id]", "Supprime une guilde par son ID."),
]

# ---- Listes par niveau de permission (Helper < Mod < Gérant) ----
PERMISSION_LEVELS = {
    "helper": 90,
    "mod": 100,
    "gerant": 110,
}

PERMISSION_LABELS = {
    "helper": "Modérateur Test",
    "mod": "Modérateur",
    "gerant": "Gérant Staff",
}

HELPER_COMMANDS = [
    ("`+warn @membre <raison>`", "Donne un avertissement à un membre."),
    ("`+warn list @membre`", "Affiche les avertissements d'un membre."),
]

MOD_COMMANDS = HELPER_COMMANDS + [
    ("`+mute @membre <durée> [raison]`", "Rend un membre muet (ex : 10m, 2h, 1j)."),
    ("`+unmute @membre`", "Retire le mute d'un membre."),
    ("`+tempban @membre <durée> [raison]`", "Bannit temporairement un membre (ex : 10m, 2h, 1j, 2w)."),
    ("`/clear [nombre]`", "Supprime un nombre de messages dans le salon."),
]

GERANT_COMMANDS = MOD_COMMANDS + [
    ("``+ban @membre [raison]`", "Bannit un membre du serveur."),
    ("``+unban <user_id> [raison]`", "Débannit un membre par son ID."),
    ("``+lock`", "Verrouille le salon (seul le staff peut y écrire)."),
    ("``+unlock`", "Déverrouille le salon."),
    ("``+unwarn @membre [n°/all]`", "Retire un ou tous les avertissements d'un membre."),
    ("``+add role @membre @role`", "Ajoute un rôle à un membre."),
    ("``+remove role @membre @role`", "Retire un rôle à un membre."),
    ("``+tempban @membre <durée> [raison]`", "Bannit temporairement un membre."),
]


def build_permission_commands_embed(cle_permission: str, couleur: discord.Color, commandes: list) -> discord.Embed:
    label = PERMISSION_LABELS[cle_permission]
    niveau = PERMISSION_LEVELS[cle_permission]
    embed = discord.Embed(
        title=f"📘 Commandes accessibles avec la permission \"{label}\"",
        description=f"Niveau : **{niveau}**\nPréfixe : **+**",
        color=couleur,
    )
    lignes = "\n".join(f"`{name}` — {desc}" for name, desc in commandes)
    embed.add_field(name="🔨 Modération", value=lignes, inline=False)
    return embed


def build_staff_commands_pages() -> list:
    """Découpe STAFF_COMMANDS en 2 pages d'embeds à peu près égales."""
    milieu = (len(STAFF_COMMANDS) + 1) // 2
    tranches = [STAFF_COMMANDS[:milieu], STAFF_COMMANDS[milieu:]]

    pages = []
    for i, items in enumerate(tranches, start=1):
        embed = discord.Embed(title=f"🛠️ Commandes Staff (page {i}/{len(tranches)})", color=discord.Color.red())
        for name, desc in items:
            embed.add_field(name=name, value=desc, inline=False)
        pages.append(embed)
    return pages


class StaffCommandsPaginator(discord.ui.View):
    """Pagination simple (◀️/▶️) pour +cmds staff. Seule la personne ayant
    lancé la commande peut naviguer."""

    def __init__(self, pages: list, author_id: int):
        super().__init__(timeout=120)
        self.pages = pages
        self.index = 0
        self.author_id = author_id
        self.message: discord.Message | None = None
        self._maj_boutons()

    def _maj_boutons(self):
        self.bouton_precedent.disabled = self.index == 0
        self.bouton_suivant.disabled = self.index == len(self.pages) - 1

    @discord.ui.button(label="◀️ Précédent", style=discord.ButtonStyle.secondary)
    async def bouton_precedent(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Seule la personne ayant utilisé la commande peut changer de page.", ephemeral=True
            )
            return
        self.index = max(0, self.index - 1)
        self._maj_boutons()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="Suivant ▶️", style=discord.ButtonStyle.secondary)
    async def bouton_suivant(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Seule la personne ayant utilisé la commande peut changer de page.", ephemeral=True
            )
            return
        self.index = min(len(self.pages) - 1, self.index + 1)
        self._maj_boutons()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


@bot.command(name="ping")
async def ping_command(ctx: commands.Context):
    latence_ms = round(bot.latency * 1000)
    embed = discord.Embed(
        title="🏓 Pong !",
        description=f"Latence du bot : **{latence_ms} ms**",
        color=discord.Color.blurple(),
    )
    await ctx.send(embed=embed)


@bot.command(name="cmds")
async def cmds_command(ctx: commands.Context, sous_commande: str = None):
    if sous_commande and sous_commande.lower() == "staff":
        if not is_staff(ctx.author):
            await ctx.send("❌ Tu n'as pas la permission de voir les commandes staff.")
            return
        pages = build_staff_commands_pages()
        view = StaffCommandsPaginator(pages, ctx.author.id)
        message = await ctx.send(embed=pages[0], view=view)
        view.message = message
        return

    if sous_commande and sous_commande.lower() == "helper":
        if not is_helper(ctx.author):
            await ctx.send("❌ Tu n'as pas la permission de voir les commandes Helper.")
            return
        embed = build_permission_commands_embed("helper", discord.Color.green(), HELPER_COMMANDS)
        await ctx.send(embed=embed)
        return

    if sous_commande and sous_commande.lower() == "mod":
        if not is_mod(ctx.author):
            await ctx.send("❌ Tu n'as pas la permission de voir les commandes Mod.")
            return
        embed = build_permission_commands_embed("mod", discord.Color.orange(), MOD_COMMANDS)
        await ctx.send(embed=embed)
        return

    if sous_commande and sous_commande.lower() in {"gerant", "gérant"}:
        if not is_gerant(ctx.author):
            await ctx.send("❌ Tu n'as pas la permission de voir les commandes Gérant.")
            return
        embed = build_permission_commands_embed("gerant", discord.Color.red(), GERANT_COMMANDS)
        await ctx.send(embed=embed)
        return

    embed = discord.Embed(title="📜 Liste des commandes", color=discord.Color.blurple())
    for name, desc in NORMAL_COMMANDS:
        embed.add_field(name=name, value=desc, inline=False)
    embed.set_footer(
        text="+cmds staff (staff) • +cmds helper / mod / gerant selon ta permission de modération."
    )
    await ctx.send(embed=embed)
 
 

# ================================================================
#                       SYSTÈME DE GUILDES
# ================================================================
#
# Les guildes sont stockées dans config.json, comme les autres systèmes du bot.
# Une guilde commence au niveau 1 avec 10 places. Elle gagne de l'XP via
# l'activité de ses membres (10 XP toutes les 60 secondes par membre).
# Niveau 5 : changement de nom disponible une seule fois.
# Niveau 10 : capacité maximale portée à 20 membres.
#
# La création est soumise à validation du staff. Les guildes publiques peuvent
# ensuite être rejointes avec /guilde join <id>, tandis que les guildes privées
# restent accessibles uniquement sur invitation.
#
# Le fondateur d'une guilde reçoit automatiquement le rôle "Chef de guilde"
# (créé automatiquement s'il n'existe pas encore). Le rôle est retiré si la
# guilde est supprimée ou si sa création est refusée par le staff.
#
# Un classement des guildes (/guilde classement) est calculé à partir d'un
# compteur de points dédié (classement_points), totalement indépendant de
# l'XP/niveau. Un administrateur peut réinitialiser ce classement
# (/guilde classement reset:True) sans jamais affecter les niveaux des guildes.

GUILD_START_MAX_MEMBERS = 10
GUILD_MAX_MEMBERS = 20
GUILD_MAX_LEVEL = 40                    # Niveau maximum d'une guilde
GUILD_XP_PER_MESSAGE = 10
GUILD_XP_COOLDOWN_SECONDS = 60
GUILD_RENAME_LEVEL = 5
GUILD_ICON_LEVEL = 1
GUILD_CAPACITY_LEVEL = 10
GUILD_ID_LENGTH = 6
CHEF_GUILDE_ROLE_NAME = "Chef de guilde"

# ---- Classement des guildes ----
# Pour rendre le classement plus difficile à grimper que la simple XP/niveau,
# les points de classement ne sont accordés qu'une fois sur
# GUILD_CLASSEMENT_HARD_INTERVAL gains d'XP valides (au lieu de systématiquement).
GUILD_CLASSEMENT_HARD_INTERVAL = 3
GUILD_CLASSEMENT_POINTS_PER_TICK = GUILD_XP_PER_MESSAGE

# ---- Paliers spéciaux de guilde ----
GUILD_HIDE_LEVEL_UNLOCK = 30      # Niveau à partir duquel le fondateur peut masquer le niveau affiché
GUILD_EXCLUSIVE_ROLE_LEVEL = 10   # Niveau qui débloque un rôle exclusif aux membres (nom de la guilde)
GUILD_EXCLUSIVE_CHANNEL_LEVEL = 20  # Niveau qui débloque un salon privé réservé aux membres

# ---- Boutique de guilde (argent gagné en montant de niveau) ----
GUILD_MONEY_PER_LEVEL = 20  # 1 niveau passé = 20$ ajoutés à la trésorerie de la guilde

# ---- Nerf d'XP selon le nombre de membres ----
# Plus une guilde a de membres, moins chaque membre rapporte d'XP individuellement,
# pour éviter qu'une grosse guilde écrase mécaniquement les petites.
# L'XP par message est divisée par la racine carrée du nombre de membres :
#   1 membre  -> 10 XP/message  (total équivalent : 1 membre "plein")
#   2 membres -> 7 XP/message   (total équivalent : ~1,4 membre)
#   4 membres -> 5 XP/message   (total équivalent : 2 membres)
#   9 membres -> 3 XP/message   (total équivalent : 3 membres)
GUILD_XP_MIN_PER_MESSAGE = 1  # plancher : un membre rapporte toujours au moins 1 XP


def guild_xp_gain_for_size(nb_membres: int) -> int:
    """XP gagnée par message selon la taille de la guilde (rendements décroissants)."""
    nb_membres = max(1, int(nb_membres))
    gain = GUILD_XP_PER_MESSAGE / (nb_membres ** 0.5)
    return max(GUILD_XP_MIN_PER_MESSAGE, int(round(gain)))


# ---- Objets utilisables (+block guild / +xp fast) ----
BLOCK_GUILD_PRICE = 600             # coût en $ pris dans la trésorerie de ta guilde (baissé de 1200$ à 600$)
BLOCK_GUILD_DURATION_MINUTES = 30   # durée du blocage d'XP infligé à la guilde ciblée
BLOCK_GUILD_COOLDOWN_HOURS = 24     # utilisable 1 fois par jour et par guilde

XP_FAST_PRICE = 800                 # coût en $ (mets 0 si tu veux le rendre gratuit)
XP_FAST_DURATION_MINUTES = 5        # durée du boost d'XP sur ta propre guilde
XP_FAST_MULTIPLIER = 3              # XP multipliée par 3 pendant le boost
XP_FAST_COOLDOWN_HOURS = 24         # utilisable 1 fois par jour et par guilde

GUILD_BADGES = {
    "nul": {"name": "🗑️ La plus nul des guildes", "price": 1},
    "epic": {"name": "⚔️ Guilde Epic", "price": 100},
    "legendaire": {"name": "🏆 Guilde Légendaire", "price": 130},
}

# ---- Quêtes de guilde (thème animé) ----
# À tout moment, 2 quêtes sont actives sur le serveur, tirées au hasard dans ce
# catalogue. TOUTES les guildes du serveur peuvent tenter de les remplir, mais
# dès qu'une guilde en complète une, celle-ci est retirée pour les autres et
# remplacée par une nouvelle quête piochée dans le catalogue.
QUETES_CATALOGUE = {
    "op_nakama": {
        "titre": "🏴‍☠️ Nakama for Life",
        "anime": "One Piece",
        "description": "Rassembler un véritable équipage : atteindre **10 membres** dans la guilde.",
        "type": "membres",
        "objectif": 10,
        "recompense_argent": 100,
    },
    "naruto_volonte": {
        "titre": "🍥 La Volonté du Feu",
        "anime": "Naruto",
        "description": "Prouver la force de la guilde : atteindre le **niveau 10**.",
        "type": "niveau",
        "objectif": 10,
        "recompense_argent": 150,
    },
    "dbz_super_saiyan": {
        "titre": "🐉 Plus fort que Freezer",
        "anime": "Dragon Ball Z",
        "description": "Dépasser ses limites : atteindre le **niveau 20**.",
        "type": "niveau",
        "objectif": 20,
        "recompense_argent": 300,
    },
    "aot_chasseurs": {
        "titre": "🗡️ Bataillon d'exploration",
        "anime": "Attack on Titan",
        "description": "S'entraîner sans relâche : cumuler **5 000 XP** de guilde.",
        "type": "xp",
        "objectif": 5000,
        "recompense_argent": 200,
    },
    "demon_slayer_pilier": {
        "titre": "👹 Devenir un Pilier",
        "anime": "Demon Slayer",
        "description": "Repousser encore plus loin l'entraînement : cumuler **10 000 XP** de guilde.",
        "type": "xp",
        "objectif": 10000,
        "recompense_argent": 400,
    },
    "mha_plus_ultra": {
        "titre": "💥 Plus Ultra !",
        "anime": "My Hero Academia",
        "description": "Dépasser ses propres records : atteindre le **niveau 25**.",
        "type": "niveau",
        "objectif": 25,
        "recompense_argent": 350,
    },
    "death_note_justice": {
        "titre": "📓 Justice Divine",
        "anime": "Death Note",
        "description": "Grimper dans les hautes sphères : cumuler **3 000 points de classement**.",
        "type": "classement_points",
        "objectif": 3000,
        "recompense_points": 150,
    },
    "jjk_domaine": {
        "titre": "🔮 Extension de Domaine",
        "anime": "Jujutsu Kaisen",
        "description": "Maîtriser une puissance ultime : atteindre le **niveau 30**.",
        "type": "niveau",
        "objectif": 30,
        "recompense_argent": 500,
    },
    "hxh_licence": {
        "titre": "🃏 Licence de Hunter",
        "anime": "Hunter x Hunter",
        "description": "Constituer une grande guilde : atteindre **20 membres**.",
        "type": "membres",
        "objectif": 20,
        "recompense_argent": 300,
    },
    "bleach_bankai": {
        "titre": "⚡ Bankai !",
        "anime": "Bleach",
        "description": "Libérer toute sa puissance : atteindre le **niveau 35**.",
        "type": "niveau",
        "objectif": 35,
        "recompense_argent": 600,
    },
    "fma_echange": {
        "titre": "⚗️ Échange Équivalent",
        "anime": "Fullmetal Alchemist",
        "description": "Investir dans la guilde : acheter **2 badges** dans la boutique.",
        "type": "badges",
        "objectif": 2,
        "recompense_argent": 100,
    },
    "tokyo_ghoul_chasse": {
        "titre": "👁️ Instinct de Chasseur",
        "anime": "Tokyo Ghoul",
        "description": "Traquer les créatures sauvages : cumuler **500 captures d'animaux** parmi les membres.",
        "type": "captures",
        "objectif": 500,
        "recompense_argent": 250,
    },
    "chainsaw_man_diable": {
        "titre": "🪚 Le Diable Tronçonneuse",
        "anime": "Chainsaw Man",
        "description": "Atteindre la puissance maximale : atteindre le **niveau 40** (niveau max).",
        "type": "niveau",
        "objectif": 40,
        "recompense_argent": 1000,
    },
    "spy_family_mission": {
        "titre": "🕵️ Mission Forger",
        "anime": "Spy x Family",
        "description": "Bâtir un trésor de guilde solide : cumuler **300$ gagnés au total**.",
        "type": "argent_total",
        "objectif": 300,
        "recompense_points": 100,
    },
}

# Cooldown XP en mémoire : {guild_id: {user_id: datetime}}
GUILD_XP_COOLDOWNS: dict[int, dict[int, datetime]] = {}


def get_server_guilds(guild_id: int) -> dict:
    guild_conf = config.setdefault(str(guild_id), {})
    return guild_conf.setdefault("guilds", {})


def save_server_guilds(guild_id: int, guilds: dict) -> None:
    guild_conf = config.setdefault(str(guild_id), {})
    guild_conf["guilds"] = guilds
    save_config(config)


def generate_guild_id(guilds: dict) -> str:
    while True:
        guild_id = "G-" + uuid.uuid4().hex[:GUILD_ID_LENGTH].upper()
        if guild_id not in guilds:
            return guild_id


def normalize_guild_visibility(value: str) -> str | None:
    value = value.strip().lower()
    if value in {"publique", "public", "pub", "ouverte", "ouvert"}:
        return "public"
    if value in {"privée", "privee", "private", "privé", "prive", "invite", "invitation"}:
        return "private"
    return None


def find_member_guild(guild_id: int, user_id: int):
    user_id = int(user_id)
    for guild_key, guild_data in get_server_guilds(guild_id).items():
        members = [int(uid) for uid in guild_data.get("members", [])]
        if user_id in members:
            return guild_key, guild_data
    return None, None


def guild_level_from_xp(xp: int) -> int:
    return min(GUILD_MAX_LEVEL, max(1, 1 + int(xp) // 100))


def guild_max_members(level: int) -> int:
    return GUILD_MAX_MEMBERS if level >= GUILD_CAPACITY_LEVEL else GUILD_START_MAX_MEMBERS


def _parse_dt(value):
    """Relit une date stockée en ISO dans config.json. Retourne None si absente/invalide."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=PARIS_TZ)
    return dt


def is_guild_blocked(guild_data: dict) -> bool:
    """True si la guilde est actuellement sous l'effet d'un +block guild (aucun gain d'XP)."""
    fin = _parse_dt(guild_data.get("block_until"))
    return fin is not None and datetime.now(PARIS_TZ) < fin


def is_guild_xp_fast(guild_data: dict) -> bool:
    """True si la guilde bénéficie actuellement d'un boost +xp fast."""
    fin = _parse_dt(guild_data.get("xpfast_until"))
    return fin is not None and datetime.now(PARIS_TZ) < fin


def cooldown_restant(last_used_iso, heures: int):
    """Retourne le timedelta restant avant réutilisation, ou None si disponible."""
    dernier = _parse_dt(last_used_iso)
    if dernier is None:
        return None
    prochain = dernier + timedelta(hours=heures)
    maintenant = datetime.now(PARIS_TZ)
    return prochain - maintenant if maintenant < prochain else None


def format_duree(delta: timedelta) -> str:
    total = int(delta.total_seconds())
    heures, reste = divmod(total, 3600)
    minutes, secondes = divmod(reste, 60)
    if heures:
        return f"{heures}h {minutes}min"
    if minutes:
        return f"{minutes}min {secondes}s"
    return f"{secondes}s"


async def get_or_create_chef_guilde_role(guild: discord.Guild) -> discord.Role | None:
    role = discord.utils.get(guild.roles, name=CHEF_GUILDE_ROLE_NAME)
    if role is None:
        try:
            role = await guild.create_role(
                name=CHEF_GUILDE_ROLE_NAME,
                color=discord.Color.dark_teal(),
                reason="Création automatique du rôle Chef de guilde",
            )
        except discord.HTTPException:
            role = None
    return role


async def ensure_guild_exclusive_role(guild: discord.Guild, guild_data: dict) -> discord.Role | None:
    """Crée (si besoin, au niveau 10) le rôle exclusif aux membres de la guilde,
    portant le nom de la guilde, et l'attribue à tous les membres actuels."""
    role_id = guild_data.get("exclusive_role_id")
    role = guild.get_role(role_id) if role_id else None

    if role is None:
        try:
            role = await guild.create_role(
                name=guild_data["name"][:100],
                color=discord.Color.blurple(),
                mentionable=True,
                reason=f"Rôle exclusif de la guilde {guild_data['name']} (niveau {GUILD_EXCLUSIVE_ROLE_LEVEL})",
            )
        except discord.HTTPException:
            return None
        guild_data["exclusive_role_id"] = role.id

    for uid in guild_data.get("members", []):
        member = guild.get_member(int(uid))
        if member and role not in member.roles:
            try:
                await member.add_roles(role, reason="Rôle exclusif de guilde")
            except discord.HTTPException:
                pass

    return role


async def ensure_guild_exclusive_channel(guild: discord.Guild, guild_data: dict) -> discord.TextChannel | None:
    """Crée (si besoin, au niveau 20) un salon textuel privé réservé aux membres
    de la guilde, visible uniquement via le rôle exclusif de la guilde."""
    channel_id = guild_data.get("guild_channel_id")
    channel = guild.get_channel(channel_id) if channel_id else None
    if channel is not None:
        return channel

    role = None
    role_id = guild_data.get("exclusive_role_id")
    if role_id:
        role = guild.get_role(role_id)
    if role is None:
        # Le salon nécessite le rôle exclusif : on le crée s'il n'existe pas encore.
        role = await ensure_guild_exclusive_role(guild, guild_data)
    if role is None:
        return None

    category = discord.utils.get(guild.categories, name="🏰 GUILDES")
    if category is None:
        try:
            category = await guild.create_category("🏰 GUILDES")
        except discord.HTTPException:
            category = None

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
    }

    slug = "".join(c if c.isalnum() else "-" for c in guild_data["name"].lower()).strip("-")[:80] or "guilde"
    try:
        channel = await guild.create_text_channel(
            f"🏰-{slug}",
            category=category,
            overwrites=overwrites,
            topic=f"Salon privé de la guilde {guild_data['name']} (débloqué au niveau {GUILD_EXCLUSIVE_CHANNEL_LEVEL}).",
            reason=f"Salon exclusif de la guilde {guild_data['name']} (niveau {GUILD_EXCLUSIVE_CHANNEL_LEVEL})",
        )
    except discord.HTTPException:
        return None

    guild_data["guild_channel_id"] = channel.id
    try:
        await channel.send(
            f"🏰 Bienvenue dans le salon privé de **{guild_data['name']}** ! "
            "Ce salon n'est visible que par les membres de la guilde."
        )
    except discord.HTTPException:
        pass

    return channel


def ensure_guild_defaults(guild_data: dict) -> bool:
    """Mise à niveau des anciennes entrées de config si nécessaire."""
    changed = False
    defaults = {
        "description": "Aucune description.",
        "owner_id": 0,
        "icon_url": None,
        "visibility": "private",
        "level": 1,
        "xp": 0,
        "classement_points": 0,
        "classement_msg_counter": 0,
        "hide_level": False,
        "exclusive_role_id": None,
        "guild_channel_id": None,
        "argent": 0,
        "argent_total": 0,
        "badges": [],
        "block_until": None,
        "block_last_used": None,
        "xpfast_until": None,
        "xpfast_last_used": None,
        "max_members": GUILD_START_MAX_MEMBERS,
        "rename_available": False,
        "verified": False,
        "members": [],
        "created_at": datetime.now(PARIS_TZ).isoformat(),
    }
    for key, value in defaults.items():
        if key not in guild_data:
            guild_data[key] = value
            changed = True

    calculated_level = guild_level_from_xp(guild_data.get("xp", 0))
    if guild_data.get("level") != calculated_level:
        guild_data["level"] = calculated_level
        changed = True

    calculated_max = guild_max_members(calculated_level)
    if guild_data.get("max_members") != calculated_max:
        guild_data["max_members"] = calculated_max
        changed = True

    # Le niveau 5 débloque le changement de nom, sauf s'il a déjà été consommé.
    if calculated_level >= GUILD_RENAME_LEVEL and "rename_available" not in guild_data:
        guild_data["rename_available"] = True
        changed = True

    return changed


def build_guild_embed(guild_id: str, guild_data: dict, discord_guild: discord.Guild) -> discord.Embed:
    ensure_guild_defaults(guild_data)
    level = guild_data["level"]
    members = guild_data.get("members", [])
    max_members = guild_max_members(level)
    visibility = "🌐 Publique" if guild_data.get("visibility") == "public" else "🔒 Sur invitation"
    status = "✅ Vérifiée" if guild_data.get("verified") else "⏳ En attente de vérification"

    embed = discord.Embed(
        title=f"🏰 {guild_data['name']}",
        description=guild_data.get("description") or "Aucune description.",
        color=discord.Color.blurple(),
    )
    if guild_data.get("icon_url"):
        embed.set_thumbnail(url=guild_data["icon_url"])

    owner = discord_guild.get_member(int(guild_data["owner_id"]))
    owner_text = owner.mention if owner else f"<@{guild_data['owner_id']}>"
    xp = int(guild_data.get("xp", 0))
    current_level_xp = xp % 100 if level < GUILD_MAX_LEVEL else 100
    progress = "MAX" if level >= GUILD_MAX_LEVEL else f"{current_level_xp}/100 XP"

    embed.add_field(name="🆔 ID", value=f"`{guild_id}`", inline=True)
    embed.add_field(name="👑 Fondateur", value=owner_text, inline=True)
    if not guild_data.get("hide_level"):
        embed.add_field(name="📈 Niveau", value=f"Niveau **{level}** • {progress}", inline=True)
    embed.add_field(name="👥 Membres", value=f"**{len(members)}/{max_members}**", inline=True)
    embed.add_field(name="🔐 Accès", value=visibility, inline=True)
    embed.add_field(name="📋 Statut", value=status, inline=True)
    embed.add_field(name="🏆 Points de classement", value=f"**{int(guild_data.get('classement_points', 0))}** pts", inline=True)
    embed.add_field(name="💰 Argent", value=f"**{int(guild_data.get('argent', 0))}$**", inline=True)
    embed.add_field(
        name="⚡ XP par message",
        value=f"**{guild_xp_gain_for_size(len(members))}** XP (réduit selon la taille)",
        inline=True,
    )

    etats = []
    if is_guild_blocked(guild_data):
        fin = _parse_dt(guild_data.get("block_until"))
        etats.append(f"🚫 Bloquée encore {format_duree(fin - datetime.now(PARIS_TZ))}")
    if is_guild_xp_fast(guild_data):
        fin = _parse_dt(guild_data.get("xpfast_until"))
        etats.append(f"⚡ Boost x{XP_FAST_MULTIPLIER} encore {format_duree(fin - datetime.now(PARIS_TZ))}")
    if etats:
        embed.add_field(name="🔔 Effets actifs", value="\n".join(etats), inline=False)

    badges = guild_data.get("badges", [])
    if badges:
        noms_badges = [GUILD_BADGES[b]["name"] for b in badges if b in GUILD_BADGES]
        embed.add_field(name="🎖️ Badges", value="\n".join(noms_badges) or "Aucun", inline=False)

    if level >= GUILD_RENAME_LEVEL:
        rename_status = "Disponible" if guild_data.get("rename_available", False) else "Déjà utilisé"
        embed.add_field(name="✏️ Changement de nom", value=rename_status, inline=True)
    if level >= GUILD_ICON_LEVEL:
        embed.add_field(name="🖼️ Icône", value="Personnalisée" if guild_data.get("icon_url") else "Aucune", inline=True)

    return embed


def get_quest_value(discord_guild_id: int, guild_data: dict, quest: dict) -> float:
    """Calcule la progression actuelle d'une guilde sur une quête donnée."""
    type_quete = quest["type"]
    if type_quete == "membres":
        return len(guild_data.get("members", []))
    if type_quete == "niveau":
        return int(guild_data.get("level", 1))
    if type_quete == "xp":
        return int(guild_data.get("xp", 0))
    if type_quete == "classement_points":
        return int(guild_data.get("classement_points", 0))
    if type_quete == "argent_total":
        return int(guild_data.get("argent_total", 0))
    if type_quete == "badges":
        return len(guild_data.get("badges", []))
    if type_quete == "captures":
        collections = config.get(str(discord_guild_id), {}).get("animal_collections", {})
        return sum(len(collections.get(str(uid), [])) for uid in guild_data.get("members", []))
    return 0


def _quete_pool_disponible(actives: list, terminees: dict) -> list:
    return [qid for qid in QUETES_CATALOGUE if qid not in actives and qid not in terminees]


async def check_and_complete_quetes(guild: discord.Guild) -> None:
    """Initialise les quêtes actives si besoin, puis vérifie si l'une des guildes
    vérifiées du serveur a rempli l'objectif d'une des 2 quêtes actives. La
    première guilde qui y parvient la remporte : la quête est alors retirée
    (les autres guildes ne peuvent plus la valider) et remplacée par une
    nouvelle quête piochée dans le catalogue."""
    guild_conf = config.setdefault(str(guild.id), {})
    quete_state = guild_conf.setdefault("quetes", {})
    actives = quete_state.setdefault("actives", [])
    terminees = quete_state.setdefault("terminees", {})

    changed = False
    while len(actives) < 2:
        dispo = _quete_pool_disponible(actives, terminees)
        if not dispo:
            break
        actives.append(random.choice(dispo))
        changed = True

    if not actives:
        if changed:
            save_config(config)
        return

    server_guilds = get_server_guilds(guild.id)
    guildes_verifiees = [(gid, data) for gid, data in server_guilds.items() if data.get("verified")]

    canal_id = guild_conf.get("quetes_channel_id")
    channel = guild.get_channel(canal_id) if canal_id else guild.system_channel

    for quest_id in list(actives):
        quest = QUETES_CATALOGUE.get(quest_id)
        if quest is None:
            actives.remove(quest_id)
            changed = True
            continue

        for guild_id_value, guild_data in guildes_verifiees:
            ensure_guild_defaults(guild_data)
            valeur = get_quest_value(guild.id, guild_data, quest)
            if valeur < quest["objectif"]:
                continue

            # Quête remplie : première guilde à y arriver, les autres ne pourront plus la faire.
            recompense_argent = quest.get("recompense_argent", 0)
            recompense_points = quest.get("recompense_points", 0)
            if recompense_argent:
                guild_data["argent"] = int(guild_data.get("argent", 0)) + recompense_argent
                guild_data["argent_total"] = int(guild_data.get("argent_total", 0)) + recompense_argent
            if recompense_points:
                guild_data["classement_points"] = int(guild_data.get("classement_points", 0)) + recompense_points

            actives.remove(quest_id)
            terminees[quest_id] = {
                "guild_id": guild_id_value,
                "guild_name": guild_data.get("name", guild_id_value),
                "date": datetime.now(PARIS_TZ).isoformat(),
            }
            changed = True

            if channel:
                recompense_txt = []
                if recompense_argent:
                    recompense_txt.append(f"{recompense_argent}$")
                if recompense_points:
                    recompense_txt.append(f"{recompense_points} pts de classement")
                try:
                    await channel.send(
                        f"🎌 Quête complétée ! **{quest['titre']}** *({quest['anime']})* a été remportée par "
                        f"**{guild_data.get('name')}** !\n🎁 Récompense : {' + '.join(recompense_txt) or 'aucune'}\n"
                        "⚠️ Cette quête n'est plus disponible pour les autres guildes."
                    )
                except discord.HTTPException:
                    pass

            dispo = _quete_pool_disponible(actives, terminees)
            if dispo:
                nouvelle_id = random.choice(dispo)
                actives.append(nouvelle_id)
                if channel:
                    nq = QUETES_CATALOGUE[nouvelle_id]
                    try:
                        await channel.send(
                            f"🆕 Nouvelle quête disponible pour toutes les guildes : "
                            f"**{nq['titre']}** *({nq['anime']})* — {nq['description']}"
                        )
                    except discord.HTTPException:
                        pass
            elif channel:
                try:
                    await channel.send("🏁 Toutes les quêtes de guilde ont désormais été complétées sur ce serveur !")
                except discord.HTTPException:
                    pass

            # On ne traite qu'une seule victoire par quête et par passage.
            break

    if changed:
        save_server_guilds(guild.id, server_guilds)
        save_config(config)


async def notifier_creation_guilde(guild: discord.Guild, guild_id: str, guild_data: dict) -> None:
    """Ping le staff dans le salon configuré (/guilde notifconfig) à chaque
    nouvelle création de guilde, pour accélérer la vérification."""
    guild_conf = config.get(str(guild.id), {})
    channel_id = guild_conf.get("guild_creation_channel_id")
    if not channel_id:
        return

    channel = guild.get_channel(channel_id)
    if channel is None:
        return

    mentions = []
    staff_role = discord.utils.get(guild.roles, name=STAFF_ROLE_NAME)
    gerant_role = discord.utils.get(guild.roles, name=GERANT_ROLE_NAME)
    if staff_role:
        mentions.append(staff_role.mention)
    if gerant_role and gerant_role != staff_role:
        mentions.append(gerant_role.mention)

    embed = build_guild_embed(guild_id, guild_data, guild)
    embed.set_footer(text="⏳ Nouvelle guilde en attente de vérification — /guilde verif")

    try:
        await channel.send(content=" ".join(mentions) or None, embed=embed)
    except discord.HTTPException:
        pass


class GuildCreateModal(discord.ui.Modal, title="Créer une guilde"):
    nom = discord.ui.TextInput(
        label="Nom de la guilde",
        placeholder="Ex : Les Dragons",
        min_length=2,
        max_length=40,
    )
    description = discord.ui.TextInput(
        label="Description",
        placeholder="Décris ta guilde en quelques mots...",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500,
    )
    visibilite = discord.ui.TextInput(
        label="Accès : publique ou privée",
        placeholder="Écris : publique ou privée",
        min_length=5,
        max_length=20,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("❌ Cette commande doit être utilisée sur un serveur.", ephemeral=True)
            return

        existing_id, _ = find_member_guild(interaction.guild.id, interaction.user.id)
        if existing_id:
            await interaction.response.send_message(
                f"❌ Tu fais déjà partie de la guilde `{existing_id}`. Quitte-la avant d'en créer une autre.",
                ephemeral=True,
            )
            return

        visibility = normalize_guild_visibility(self.visibilite.value)
        if visibility is None:
            await interaction.response.send_message(
                "❌ Accès invalide. Utilise exactement **publique** ou **privée**.",
                ephemeral=True,
            )
            return

        name = self.nom.value.strip()
        description = self.description.value.strip() or "Aucune description."
        guilds = get_server_guilds(interaction.guild.id)

        if any(data.get("name", "").casefold() == name.casefold() for data in guilds.values()):
            await interaction.response.send_message("❌ Une guilde porte déjà ce nom.", ephemeral=True)
            return

        guild_id = generate_guild_id(guilds)
        guilds[guild_id] = {
            "name": name,
            "description": description,
            "owner_id": interaction.user.id,
            "icon_url": None,
            "visibility": visibility,
            "level": 1,
            "xp": 0,
            "classement_points": 0,
            "max_members": GUILD_START_MAX_MEMBERS,
            "rename_available": False,
            "verified": False,
            "members": [interaction.user.id],
            "created_at": datetime.now(PARIS_TZ).isoformat(),
        }
        save_server_guilds(interaction.guild.id, guilds)

        # Attribue le rôle Chef de guilde au fondateur (créé automatiquement si besoin).
        role = await get_or_create_chef_guilde_role(interaction.guild)
        if role:
            try:
                await interaction.user.add_roles(role, reason=f"Fondateur de la guilde {name}")
            except discord.HTTPException:
                pass

        embed = build_guild_embed(guild_id, guilds[guild_id], interaction.guild)
        embed.set_footer(text="⏳ Ta guilde a été créée et attend la vérification du staff.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

        await notifier_creation_guilde(interaction.guild, guild_id, guilds[guild_id])


class GuildInviteView(discord.ui.View):
    def __init__(self, guild_id: str, target_user_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.target_user_id = target_user_id

    @discord.ui.button(label="✅ Accepter", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_user_id:
            await interaction.response.send_message("❌ Cette invitation ne t'est pas destinée.", ephemeral=True)
            return

        if interaction.guild is None:
            await interaction.response.send_message("❌ Serveur introuvable.", ephemeral=True)
            return

        guilds = get_server_guilds(interaction.guild.id)
        guild_data = guilds.get(self.guild_id)
        if not guild_data:
            await interaction.response.send_message("❌ Cette guilde n'existe plus.", ephemeral=True)
            return
        ensure_guild_defaults(guild_data)

        if not guild_data.get("verified"):
            await interaction.response.send_message("❌ Cette guilde n'est pas encore vérifiée par le staff.", ephemeral=True)
            return

        current_id, _ = find_member_guild(interaction.guild.id, interaction.user.id)
        if current_id:
            await interaction.response.send_message(
                f"❌ Tu fais déjà partie de la guilde `{current_id}`.", ephemeral=True
            )
            return

        max_members = guild_max_members(guild_data["level"])
        if len(guild_data.get("members", [])) >= max_members:
            await interaction.response.send_message("❌ Cette guilde est complète.", ephemeral=True)
            return

        guild_data.setdefault("members", []).append(interaction.user.id)
        save_server_guilds(interaction.guild.id, guilds)

        exclusive_role_id = guild_data.get("exclusive_role_id")
        if exclusive_role_id:
            role = interaction.guild.get_role(exclusive_role_id)
            if role:
                try:
                    await interaction.user.add_roles(role, reason="Rejoint la guilde")
                except discord.HTTPException:
                    pass

        self.stop()
        await interaction.response.edit_message(
            content=f"✅ Tu as rejoint **{guild_data['name']}** !",
            embed=None,
            view=None,
        )

        try:
            await check_and_complete_quetes(interaction.guild)
        except discord.HTTPException:
            pass

    @discord.ui.button(label="❌ Refuser", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_user_id:
            await interaction.response.send_message("❌ Cette invitation ne t'est pas destinée.", ephemeral=True)
            return
        self.stop()
        await interaction.response.edit_message(content="❌ Invitation refusée.", embed=None, view=None)


class GuildVerificationView(discord.ui.View):
    def __init__(self, guild_id: int, pending_ids: list[str]):
        super().__init__(timeout=300)
        self.discord_guild_id = guild_id
        self.pending_ids = pending_ids

        for guild_id_value in pending_ids[:5]:
            self.add_item(
                discord.ui.Button(
                    label=f"Valider {guild_id_value}",
                    style=discord.ButtonStyle.success,
                    custom_id=f"guilde_verify:{guild_id_value}",
                )
            )
            self.add_item(
                discord.ui.Button(
                    label=f"Refuser {guild_id_value}",
                    style=discord.ButtonStyle.danger,
                    custom_id=f"guilde_reject:{guild_id_value}",
                )
            )


async def show_guild_verification_panel(interaction: discord.Interaction):
    guilds = get_server_guilds(interaction.guild.id)
    pending = []
    for guild_id_value, guild_data in guilds.items():
        if ensure_guild_defaults(guild_data) or guild_data.get("verified") is False:
            if not guild_data.get("verified"):
                pending.append(guild_id_value)
    save_server_guilds(interaction.guild.id, guilds)

    if not pending:
        await interaction.response.send_message("✅ Aucune guilde n'attend actuellement de vérification.", ephemeral=True)
        return

    lines = []
    for guild_id_value in pending[:5]:
        data = guilds[guild_id_value]
        owner = interaction.guild.get_member(int(data["owner_id"]))
        owner_text = owner.mention if owner else f"<@{data['owner_id']}>"
        visibility = "publique" if data.get("visibility") == "public" else "privée"
        lines.append(
            f"**{data['name']}** — `{guild_id_value}`\n"
            f"👑 {owner_text} • 👥 {len(data.get('members', []))}/10 • 🔐 {visibility}\n"
            f"> {data.get('description', 'Aucune description.')[:200]}"
        )

    embed = discord.Embed(
        title="🛡️ Vérification des guildes",
        description="\n\n".join(lines),
        color=discord.Color.orange(),
    )
    if len(pending) > 5:
        embed.set_footer(text=f"5 premières affichées • {len(pending)} guildes en attente au total.")
    else:
        embed.set_footer(text=f"{len(pending)} guilde(s) en attente.")

    await interaction.response.send_message(
        embed=embed,
        view=GuildVerificationView(interaction.guild.id, pending[:5]),
        ephemeral=True,
    )


@bot.listen("on_interaction")
async def on_guild_verification_interaction(interaction: discord.Interaction):
    if interaction.type != discord.InteractionType.component or interaction.guild is None:
        return

    custom_id = interaction.data.get("custom_id", "")
    if not (custom_id.startswith("guilde_verify:") or custom_id.startswith("guilde_reject:")):
        return

    if not is_staff(interaction.user):
        await interaction.response.send_message("❌ Cette action est réservée au staff.", ephemeral=True)
        return

    action, guild_id_value = custom_id.split(":", 1)
    guilds = get_server_guilds(interaction.guild.id)
    guild_data = guilds.get(guild_id_value)
    if not guild_data:
        await interaction.response.send_message("❌ Cette guilde n'existe plus.", ephemeral=True)
        return

    if action == "guilde_verify":
        guild_data["verified"] = True
        save_server_guilds(interaction.guild.id, guilds)
        await interaction.response.send_message(
            f"✅ La guilde **{guild_data['name']}** (`{guild_id_value}`) a été vérifiée.",
            ephemeral=True,
        )
    else:
        name = guild_data.get("name", guild_id_value)
        owner_id = guild_data.get("owner_id")
        del guilds[guild_id_value]
        save_server_guilds(interaction.guild.id, guilds)

        if owner_id:
            owner_member = interaction.guild.get_member(int(owner_id))
            role = discord.utils.get(interaction.guild.roles, name=CHEF_GUILDE_ROLE_NAME)
            if owner_member and role and role in owner_member.roles:
                try:
                    await owner_member.remove_roles(role, reason=f"Demande de guilde {name} refusée")
                except discord.HTTPException:
                    pass

        await interaction.response.send_message(
            f"❌ La demande de guilde **{name}** (`{guild_id_value}`) a été refusée et supprimée.",
            ephemeral=True,
        )


@bot.tree.command(name="guilde", description="Gestion des guildes")
async def guilde_root(interaction: discord.Interaction):
    await interaction.response.send_message(
        "🏰 Utilise une sous-commande : `/guilde create`, `/guilde info`, `/guilde invite`, `/guilde join`, "
        "`/guilde leave`, `/guilde members`, `/guilde rename`, `/guilde icon`, `/guilde classement`, "
        "`/guilde boutique`, `/guilde acheter`, `/guilde quetes` ou `/guilde verif`.",
        ephemeral=True,
    )


# Groupe réel utilisé par Discord pour les sous-commandes.
# La commande racine ci-dessus est retirée juste après sa définition pour laisser
# la place au groupe app_commands portant le même nom.
bot.tree.remove_command("guilde")
guilde_group = app_commands.Group(name="guilde", description="Gestion des guildes")


@guilde_group.command(name="create", description="Crée une nouvelle guilde")
async def guilde_create_cmd(interaction: discord.Interaction):
    await interaction.response.send_modal(GuildCreateModal())


@guilde_group.command(name="info", description="Affiche les informations d'une guilde")
@app_commands.describe(id="ID de la guilde (optionnel si tu en fais partie)")
async def guilde_info_cmd(interaction: discord.Interaction, id: str = None):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    guilds = get_server_guilds(interaction.guild.id)
    if id:
        guild_id_value = id.upper()
        guild_data = guilds.get(guild_id_value)
    else:
        guild_id_value, guild_data = find_member_guild(interaction.guild.id, interaction.user.id)

    if not guild_data:
        await interaction.response.send_message("❌ Guilde introuvable. Indique un ID valide ou rejoins une guilde.", ephemeral=True)
        return

    changed = ensure_guild_defaults(guild_data)
    if changed:
        save_server_guilds(interaction.guild.id, guilds)
    await interaction.response.send_message(embed=build_guild_embed(guild_id_value, guild_data, interaction.guild))


@guilde_group.command(name="invite", description="Invite un membre dans ta guilde")
@app_commands.describe(membre="Membre à inviter")
async def guilde_invite_cmd(interaction: discord.Interaction, membre: discord.Member):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    guild_id_value, guild_data = find_member_guild(interaction.guild.id, interaction.user.id)
    if not guild_data:
        await interaction.response.send_message("❌ Tu n'es dans aucune guilde.", ephemeral=True)
        return
    if int(guild_data["owner_id"]) != interaction.user.id:
        await interaction.response.send_message("❌ Seul le fondateur peut inviter des membres.", ephemeral=True)
        return
    if not guild_data.get("verified"):
        await interaction.response.send_message("❌ Ta guilde doit être vérifiée par le staff avant d'inviter des membres.", ephemeral=True)
        return
    if membre.bot:
        await interaction.response.send_message("❌ Tu ne peux pas inviter un bot.", ephemeral=True)
        return
    if membre.id == interaction.user.id:
        await interaction.response.send_message("❌ Tu es déjà dans cette guilde.", ephemeral=True)
        return
    if membre.id in [int(uid) for uid in guild_data.get("members", [])]:
        await interaction.response.send_message("❌ Ce membre est déjà dans ta guilde.", ephemeral=True)
        return
    other_id, _ = find_member_guild(interaction.guild.id, membre.id)
    if other_id:
        await interaction.response.send_message(f"❌ Ce membre est déjà dans la guilde `{other_id}`.", ephemeral=True)
        return
    if len(guild_data.get("members", [])) >= guild_max_members(guild_data["level"]):
        await interaction.response.send_message("❌ Ta guilde est complète.", ephemeral=True)
        return

    embed = discord.Embed(
        title="🏰 Invitation à une guilde",
        description=(
            f"{membre.mention}, **{interaction.user.display_name}** t'invite à rejoindre "
            f"**{guild_data['name']}** (`{guild_id_value}`).\n\n"
            f"{guild_data.get('description', 'Aucune description.')}"
        ),
        color=discord.Color.blurple(),
    )
    await interaction.response.send_message(
        content=membre.mention,
        embed=embed,
        view=GuildInviteView(guild_id_value, membre.id),
    )


@guilde_group.command(name="join", description="Rejoins une guilde publique avec son ID")
@app_commands.describe(id="ID de la guilde publique")
async def guilde_join_cmd(interaction: discord.Interaction, id: str):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    guilds = get_server_guilds(interaction.guild.id)
    guild_id_value = id.upper()
    guild_data = guilds.get(guild_id_value)
    if not guild_data:
        await interaction.response.send_message("❌ Guilde introuvable.", ephemeral=True)
        return
    ensure_guild_defaults(guild_data)
    if not guild_data.get("verified"):
        await interaction.response.send_message("❌ Cette guilde n'est pas encore vérifiée par le staff.", ephemeral=True)
        return
    if guild_data.get("visibility") != "public":
        await interaction.response.send_message("❌ Cette guilde est privée : demande au fondateur de t'inviter.", ephemeral=True)
        return
    current_id, _ = find_member_guild(interaction.guild.id, interaction.user.id)
    if current_id:
        await interaction.response.send_message(f"❌ Tu fais déjà partie de la guilde `{current_id}`.", ephemeral=True)
        return
    if len(guild_data.get("members", [])) >= guild_max_members(guild_data["level"]):
        await interaction.response.send_message("❌ Cette guilde est complète.", ephemeral=True)
        return

    guild_data.setdefault("members", []).append(interaction.user.id)
    save_server_guilds(interaction.guild.id, guilds)

    exclusive_role_id = guild_data.get("exclusive_role_id")
    if exclusive_role_id:
        role = interaction.guild.get_role(exclusive_role_id)
        if role:
            try:
                await interaction.user.add_roles(role, reason="Rejoint la guilde")
            except discord.HTTPException:
                pass

    await interaction.response.send_message(f"✅ Tu as rejoint **{guild_data['name']}** !")

    try:
        await check_and_complete_quetes(interaction.guild)
    except discord.HTTPException:
        pass


@guilde_group.command(name="leave", description="Quitte ta guilde actuelle")
async def guilde_leave_cmd(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    guild_id_value, guild_data = find_member_guild(interaction.guild.id, interaction.user.id)
    if not guild_data:
        await interaction.response.send_message("❌ Tu ne fais partie d'aucune guilde.", ephemeral=True)
        return
    if int(guild_data["owner_id"]) == interaction.user.id:
        await interaction.response.send_message(
            "❌ Tu es le fondateur. Tu ne peux pas simplement quitter la guilde : utilise `/guilde delete` si tu veux la supprimer (ou transfère la propriété dans une future version).",
            ephemeral=True,
        )
        return
    guild_data["members"] = [uid for uid in guild_data.get("members", []) if int(uid) != interaction.user.id]
    save_server_guilds(interaction.guild.id, get_server_guilds(interaction.guild.id))

    exclusive_role_id = guild_data.get("exclusive_role_id")
    if exclusive_role_id:
        role = interaction.guild.get_role(exclusive_role_id)
        if role and role in interaction.user.roles:
            try:
                await interaction.user.remove_roles(role, reason="A quitté la guilde")
            except discord.HTTPException:
                pass

    await interaction.response.send_message(f"✅ Tu as quitté la guilde **{guild_data['name']}**.", ephemeral=True)


@guilde_group.command(name="members", description="Affiche les membres de ta guilde")
async def guilde_members_cmd(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    guild_id_value, guild_data = find_member_guild(interaction.guild.id, interaction.user.id)
    if not guild_data:
        await interaction.response.send_message("❌ Tu ne fais partie d'aucune guilde.", ephemeral=True)
        return

    lines = []
    for uid in guild_data.get("members", []):
        member = interaction.guild.get_member(int(uid))
        if int(uid) == int(guild_data["owner_id"]):
            role = "👑 Fondateur"
        else:
            role = "👤 Membre"
        lines.append(f"{role} — {member.mention if member else f'<@{uid}>'}")

    embed = discord.Embed(
        title=f"👥 Membres — {guild_data['name']}",
        description="\n".join(lines) or "Aucun membre.",
        color=discord.Color.blurple(),
    )
    embed.set_footer(text=f"Guilde {guild_id_value} • {len(lines)}/{guild_max_members(guild_data['level'])}")
    await interaction.response.send_message(embed=embed)


@guilde_group.command(name="rename", description="Change le nom de ta guilde (niveau 5, une seule fois)")
@app_commands.describe(nom="Nouveau nom de la guilde")
async def guilde_rename_cmd(interaction: discord.Interaction, nom: str):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    guild_id_value, guild_data = find_member_guild(interaction.guild.id, interaction.user.id)
    if not guild_data:
        await interaction.response.send_message("❌ Tu ne fais partie d'aucune guilde.", ephemeral=True)
        return
    if int(guild_data["owner_id"]) != interaction.user.id:
        await interaction.response.send_message("❌ Seul le fondateur peut changer le nom.", ephemeral=True)
        return
    if guild_data["level"] < GUILD_RENAME_LEVEL:
        await interaction.response.send_message("❌ Le changement de nom se débloque au niveau 5.", ephemeral=True)
        return
    if not guild_data.get("rename_available", False):
        await interaction.response.send_message("❌ Le changement de nom unique a déjà été utilisé.", ephemeral=True)
        return

    nouveau_nom = nom.strip()
    if len(nouveau_nom) < 2 or len(nouveau_nom) > 40:
        await interaction.response.send_message("❌ Le nom doit contenir entre 2 et 40 caractères.", ephemeral=True)
        return
    guilds = get_server_guilds(interaction.guild.id)
    if any(key != guild_id_value and data.get("name", "").casefold() == nouveau_nom.casefold() for key, data in guilds.items()):
        await interaction.response.send_message("❌ Une autre guilde porte déjà ce nom.", ephemeral=True)
        return

    ancien_nom = guild_data["name"]
    guild_data["name"] = nouveau_nom
    guild_data["rename_available"] = False
    save_server_guilds(interaction.guild.id, guilds)
    await interaction.response.send_message(
        f"✅ La guilde `{ancien_nom}` s'appelle maintenant **{nouveau_nom}**.\nLe changement de nom unique a été consommé.",
        ephemeral=True,
    )


@guilde_group.command(name="icon", description="Définit l'icône personnalisée de ta guilde")
@app_commands.describe(url="URL directe de l'image")
async def guilde_icon_cmd(interaction: discord.Interaction, url: str):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return
    guild_id_value, guild_data = find_member_guild(interaction.guild.id, interaction.user.id)
    if not guild_data:
        await interaction.response.send_message("❌ Tu ne fais partie d'aucune guilde.", ephemeral=True)
        return
    if int(guild_data["owner_id"]) != interaction.user.id:
        await interaction.response.send_message("❌ Seul le fondateur peut modifier l'icône.", ephemeral=True)
        return
    if guild_data["level"] < GUILD_ICON_LEVEL:
        await interaction.response.send_message("❌ L'icône personnalisée se débloque au niveau 1.", ephemeral=True)
        return
    if not re.match(r"^https?://", url.strip(), re.IGNORECASE):
        await interaction.response.send_message("❌ Fournis une URL d'image commençant par `http://` ou `https://`.", ephemeral=True)
        return

    guild_data["icon_url"] = url.strip()
    save_server_guilds(interaction.guild.id, get_server_guilds(interaction.guild.id))
    await interaction.response.send_message("✅ L'icône personnalisée de la guilde a été mise à jour.", ephemeral=True)


@guilde_group.command(name="classement", description="Affiche le classement des guildes (option admin : reset)")
@app_commands.describe(reset="[Admin] Réinitialise le classement des guildes SANS toucher aux niveaux/XP")
async def guilde_classement_cmd(interaction: discord.Interaction, reset: bool = False):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return

    guilds = get_server_guilds(interaction.guild.id)

    if reset:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "❌ Seul un membre avec la permission **Administrateur** peut réinitialiser le classement.",
                ephemeral=True,
            )
            return
        for guild_data in guilds.values():
            guild_data["classement_points"] = 0
        save_server_guilds(interaction.guild.id, guilds)
        await interaction.response.send_message(
            "✅ Le classement des guildes a été réinitialisé.\n"
            "ℹ️ Les niveaux et l'XP des guildes n'ont **pas** été affectés.",
            ephemeral=True,
        )
        return

    changed = False
    for guild_data in guilds.values():
        if ensure_guild_defaults(guild_data):
            changed = True
    if changed:
        save_server_guilds(interaction.guild.id, guilds)

    classees = [(gid, data) for gid, data in guilds.items() if data.get("verified")]
    if not classees:
        await interaction.response.send_message("Aucune guilde vérifiée pour le moment.", ephemeral=True)
        return

    classees.sort(
        key=lambda item: (
            -int(item[1].get("classement_points", 0)),
            -int(item[1].get("level", 1)),
            item[1].get("name", ""),
        )
    )

    medailles = ["🥇", "🥈", "🥉"]
    lignes = []
    for i, (gid, data) in enumerate(classees[:10], start=1):
        prefix = medailles[i - 1] if i <= 3 else f"**#{i}**"
        points = int(data.get("classement_points", 0))
        lignes.append(f"{prefix} **{data['name']}** (`{gid}`) — {points} pts • Niveau {data.get('level', 1)}")

    embed = discord.Embed(
        title="🏆 Classement des guildes",
        description="\n".join(lignes),
        color=discord.Color.gold(),
    )
    embed.set_footer(text="Basé sur l'activité des membres • Réinitialisable par un admin (n'affecte pas les niveaux)")
    await interaction.response.send_message(embed=embed)


@guilde_group.command(name="boutique", description="Affiche la boutique de badges de guilde")
async def guilde_boutique_cmd(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return

    _, guild_data = find_member_guild(interaction.guild.id, interaction.user.id)
    argent = int(guild_data.get("argent", 0)) if guild_data else None
    badges_possedes = guild_data.get("badges", []) if guild_data else []

    lignes = []
    for cle, info in GUILD_BADGES.items():
        statut = "✅ Déjà possédé" if cle in badges_possedes else f"{info['price']}$"
        lignes.append(f"**{info['name']}** — {statut}")

    embed = discord.Embed(
        title="🛒 Boutique de badges de guilde",
        description="\n".join(lignes),
        color=discord.Color.blurple(),
    )
    if guild_data is not None:
        embed.set_footer(text=f"💰 Trésorerie de {guild_data['name']} : {argent}$ • Achat via /guilde acheter (fondateur uniquement)")
    else:
        embed.set_footer(text="Rejoins ou crée une guilde pour pouvoir acheter des badges.")

    await interaction.response.send_message(embed=embed, ephemeral=True)


@guilde_group.command(name="acheter", description="[Fondateur] Achète un badge dans la boutique pour ta guilde")
@app_commands.describe(badge="Badge à acheter")
@app_commands.choices(badge=[
    app_commands.Choice(name=f"{info['name']} — {info['price']}$", value=cle)
    for cle, info in GUILD_BADGES.items()
])
async def guilde_acheter_cmd(interaction: discord.Interaction, badge: app_commands.Choice[str]):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return

    guild_id_value, guild_data = find_member_guild(interaction.guild.id, interaction.user.id)
    if not guild_data:
        await interaction.response.send_message("❌ Tu ne fais partie d'aucune guilde.", ephemeral=True)
        return
    if int(guild_data["owner_id"]) != interaction.user.id:
        await interaction.response.send_message("❌ Seul le fondateur peut acheter des badges pour la guilde.", ephemeral=True)
        return

    cle_badge = badge.value
    info_badge = GUILD_BADGES.get(cle_badge)
    if info_badge is None:
        await interaction.response.send_message("❌ Badge introuvable.", ephemeral=True)
        return

    badges_possedes = guild_data.setdefault("badges", [])
    if cle_badge in badges_possedes:
        await interaction.response.send_message(
            f"⚠️ Ta guilde possède déjà le badge **{info_badge['name']}**.", ephemeral=True
        )
        return

    argent = int(guild_data.get("argent", 0))
    if argent < info_badge["price"]:
        await interaction.response.send_message(
            f"❌ Trésorerie insuffisante : il te faut **{info_badge['price']}$** mais ta guilde n'a que **{argent}$**.",
            ephemeral=True,
        )
        return

    guild_data["argent"] = argent - info_badge["price"]
    badges_possedes.append(cle_badge)
    save_server_guilds(interaction.guild.id, get_server_guilds(interaction.guild.id))

    await interaction.response.send_message(
        f"✅ Ta guilde a acheté le badge **{info_badge['name']}** pour **{info_badge['price']}$** !\n"
        f"💰 Trésorerie restante : **{guild_data['argent']}$**",
    )

    try:
        await check_and_complete_quetes(interaction.guild)
    except discord.HTTPException:
        pass


@guilde_group.command(name="quetes", description="Affiche les quêtes de guilde actives et déjà complétées")
async def guilde_quetes_cmd(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return

    await check_and_complete_quetes(interaction.guild)

    state = config.get(str(interaction.guild.id), {}).get("quetes", {})
    actives = state.get("actives", [])
    terminees = state.get("terminees", {})

    embed = discord.Embed(
        title="🎌 Quêtes de guilde",
        description="2 quêtes actives à la fois. La première guilde qui remplit l'objectif la remporte "
        "et les autres guildes ne peuvent plus la valider.",
        color=discord.Color.purple(),
    )

    if actives:
        for qid in actives:
            q = QUETES_CATALOGUE.get(qid)
            if not q:
                continue
            recompense = []
            if q.get("recompense_argent"):
                recompense.append(f"{q['recompense_argent']}$")
            if q.get("recompense_points"):
                recompense.append(f"{q['recompense_points']} pts de classement")
            embed.add_field(
                name=f"{q['titre']} — {q['anime']}",
                value=f"{q['description']}\n🎁 Récompense : {' + '.join(recompense) or 'aucune'}",
                inline=False,
            )
    else:
        embed.add_field(name="🟢 Quêtes en cours", value="Aucune quête active pour le moment.", inline=False)

    if terminees:
        dernieres = list(terminees.items())[-8:]
        lignes = []
        for qid, info in dernieres:
            q = QUETES_CATALOGUE.get(qid, {})
            lignes.append(f"✅ **{q.get('titre', qid)}** — remportée par **{info['guild_name']}**")
        embed.add_field(name="🏁 Dernières quêtes complétées", value="\n".join(lignes), inline=False)

    embed.set_footer(text=f"{len(terminees)}/{len(QUETES_CATALOGUE)} quêtes complétées au total sur ce serveur")
    await interaction.response.send_message(embed=embed)


@guilde_group.command(name="quetesconfig", description="[Staff] Définit le salon d'annonce des quêtes de guilde")
@app_commands.describe(salon="Salon où seront annoncées les quêtes complétées et les nouvelles quêtes")
async def guilde_quetesconfig_cmd(interaction: discord.Interaction, salon: discord.TextChannel):
    if not is_staff(interaction.user):
        await interaction.response.send_message("❌ Cette commande est réservée au staff.", ephemeral=True)
        return
    if interaction.guild is None:
        await interaction.response.send_message("❌ Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return

    guild_conf = config.setdefault(str(interaction.guild.id), {})
    guild_conf["quetes_channel_id"] = salon.id
    save_config(config)

    await interaction.response.send_message(
        f"✅ Les quêtes de guilde (complétions et nouvelles quêtes) seront désormais annoncées dans {salon.mention}.",
        ephemeral=True,
    )


@guilde_group.command(name="verif", description="[Staff] Affiche les guildes en attente de vérification")
async def guilde_verif_cmd(interaction: discord.Interaction):
    if not is_staff(interaction.user):
        await interaction.response.send_message("❌ Cette commande est réservée au staff.", ephemeral=True)
        return
    await show_guild_verification_panel(interaction)


@guilde_group.command(name="delete", description="[Staff] Supprime une guilde avec son ID")
@app_commands.describe(id="ID de la guilde à supprimer")
async def guilde_delete_cmd(interaction: discord.Interaction, id: str):
    if not is_staff(interaction.user):
        await interaction.response.send_message("❌ Cette commande est réservée au staff.", ephemeral=True)
        return
    if interaction.guild is None:
        await interaction.response.send_message("❌ Cette commande doit être utilisée sur un serveur.", ephemeral=True)
        return

    guilds = get_server_guilds(interaction.guild.id)
    guild_id_value = id.upper()
    guild_data = guilds.get(guild_id_value)
    if not guild_data:
        await interaction.response.send_message("❌ Guilde introuvable.", ephemeral=True)
        return

    name = guild_data.get("name", guild_id_value)
    owner_id = guild_data.get("owner_id")
    del guilds[guild_id_value]
    save_server_guilds(interaction.guild.id, guilds)

    if owner_id:
        owner_member = interaction.guild.get_member(int(owner_id))
        role = discord.utils.get(interaction.guild.roles, name=CHEF_GUILDE_ROLE_NAME)
        if owner_member and role and role in owner_member.roles:
            try:
                await owner_member.remove_roles(role, reason=f"Suppression de la guilde {name}")
            except discord.HTTPException:
                pass

    await interaction.response.send_message(f"🗑️ La guilde **{name}** (`{guild_id_value}`) a été supprimée.", ephemeral=True)


bot.tree.add_command(guilde_group)


# ================================================================
#           OBJETS DE GUILDE (+block guild / +xp fast)
# ================================================================
#
# +block guild <ID>  : bloque l'XP d'une guilde adverse pendant 30 min.
#                      Coûte 1200$ dans la trésorerie de TA guilde,
#                      utilisable 1 seule fois par jour et par guilde.
# +xp fast           : booste l'XP de TA propre guilde pendant 5 min.
#                      Utilisable 1 seule fois par jour et par guilde.
#
# Dans les deux cas, seul le fondateur (chef de guilde) peut déclencher l'objet.

@bot.command(name="block")
async def block_command(ctx: commands.Context, cible_type: str = None, guild_id: str = None):
    if ctx.guild is None:
        return

    if cible_type is None or cible_type.lower() != "guild" or guild_id is None:
        await ctx.send("❌ Utilisation : `+block guild <ID de la guilde>`")
        return

    mon_id, ma_guilde = find_member_guild(ctx.guild.id, ctx.author.id)
    if not ma_guilde:
        await ctx.send("❌ Tu ne fais partie d'aucune guilde.")
        return
    if int(ma_guilde["owner_id"]) != ctx.author.id:
        await ctx.send("❌ Seul le fondateur de la guilde peut utiliser cet objet.")
        return
    if not ma_guilde.get("verified"):
        await ctx.send("❌ Ta guilde doit être vérifiée par le staff pour utiliser cet objet.")
        return

    guilds = get_server_guilds(ctx.guild.id)
    cible_id = guild_id.upper()
    cible = guilds.get(cible_id)
    if not cible:
        await ctx.send("❌ Guilde introuvable. Vérifie l'ID (visible avec `/guilde info`).")
        return
    if cible_id == mon_id:
        await ctx.send("❌ Tu ne peux pas bloquer ta propre guilde.")
        return
    ensure_guild_defaults(cible)

    # Cooldown : 1 utilisation par jour et par guilde attaquante.
    restant = cooldown_restant(ma_guilde.get("block_last_used"), BLOCK_GUILD_COOLDOWN_HOURS)
    if restant:
        await ctx.send(f"⏳ Ta guilde a déjà utilisé cet objet aujourd'hui. Réessaie dans **{format_duree(restant)}**.")
        return

    argent = int(ma_guilde.get("argent", 0))
    if argent < BLOCK_GUILD_PRICE:
        await ctx.send(
            f"❌ Trésorerie insuffisante : il faut **{BLOCK_GUILD_PRICE}$** mais ta guilde n'a que **{argent}$**."
        )
        return

    if is_guild_blocked(cible):
        await ctx.send(f"⚠️ **{cible['name']}** est déjà sous l'effet d'un blocage.")
        return

    maintenant = datetime.now(PARIS_TZ)
    ma_guilde["argent"] = argent - BLOCK_GUILD_PRICE
    ma_guilde["block_last_used"] = maintenant.isoformat()
    cible["block_until"] = (maintenant + timedelta(minutes=BLOCK_GUILD_DURATION_MINUTES)).isoformat()
    save_server_guilds(ctx.guild.id, guilds)

    embed = discord.Embed(
        title="🚫 Guilde bloquée !",
        description=(
            f"**{ma_guilde['name']}** a bloqué **{cible['name']}** (`{cible_id}`) !\n\n"
            f"⏱️ Plus aucun gain d'XP pour cette guilde pendant **{BLOCK_GUILD_DURATION_MINUTES} minutes**."
        ),
        color=discord.Color.dark_red(),
    )
    embed.set_footer(text=f"Coût : {BLOCK_GUILD_PRICE}$ • Trésorerie restante : {ma_guilde['argent']}$")
    await ctx.send(embed=embed)


@bot.command(name="xp")
async def xp_command(ctx: commands.Context, sous_commande: str = None):
    if ctx.guild is None:
        return

    if sous_commande is None or sous_commande.lower() != "fast":
        await ctx.send("❌ Utilisation : `+xp fast` (booste l'XP de ta propre guilde).")
        return

    mon_id, ma_guilde = find_member_guild(ctx.guild.id, ctx.author.id)
    if not ma_guilde:
        await ctx.send("❌ Tu ne fais partie d'aucune guilde.")
        return
    if int(ma_guilde["owner_id"]) != ctx.author.id:
        await ctx.send("❌ Seul le fondateur de la guilde peut utiliser cet objet.")
        return
    if not ma_guilde.get("verified"):
        await ctx.send("❌ Ta guilde doit être vérifiée par le staff pour utiliser cet objet.")
        return

    ensure_guild_defaults(ma_guilde)

    if is_guild_blocked(ma_guilde):
        await ctx.send("❌ Ta guilde est actuellement bloquée : impossible de booster l'XP pour le moment.")
        return
    if is_guild_xp_fast(ma_guilde):
        await ctx.send("⚠️ Un boost d'XP est déjà actif sur ta guilde.")
        return

    restant = cooldown_restant(ma_guilde.get("xpfast_last_used"), XP_FAST_COOLDOWN_HOURS)
    if restant:
        await ctx.send(f"⏳ Ta guilde a déjà utilisé cet objet aujourd'hui. Réessaie dans **{format_duree(restant)}**.")
        return

    argent = int(ma_guilde.get("argent", 0))
    if XP_FAST_PRICE and argent < XP_FAST_PRICE:
        await ctx.send(
            f"❌ Trésorerie insuffisante : il faut **{XP_FAST_PRICE}$** mais ta guilde n'a que **{argent}$**."
        )
        return

    maintenant = datetime.now(PARIS_TZ)
    if XP_FAST_PRICE:
        ma_guilde["argent"] = argent - XP_FAST_PRICE
    ma_guilde["xpfast_last_used"] = maintenant.isoformat()
    ma_guilde["xpfast_until"] = (maintenant + timedelta(minutes=XP_FAST_DURATION_MINUTES)).isoformat()
    save_server_guilds(ctx.guild.id, get_server_guilds(ctx.guild.id))

    embed = discord.Embed(
        title="⚡ Boost d'XP activé !",
        description=(
            f"**{ma_guilde['name']}** gagne **x{XP_FAST_MULTIPLIER} d'XP** pendant "
            f"**{XP_FAST_DURATION_MINUTES} minutes** !\n\nÀ vos claviers, c'est le moment de parler 💬"
        ),
        color=discord.Color.green(),
    )
    if XP_FAST_PRICE:
        embed.set_footer(text=f"Coût : {XP_FAST_PRICE}$ • Trésorerie restante : {ma_guilde['argent']}$")
    await ctx.send(embed=embed)


# ================================================================
#                        +lock / +unlock
# ================================================================

@bot.command(name="lock")
async def lock_command(ctx: commands.Context):
    if not is_gerant(ctx.author):
        await ctx.send("❌ Cette commande est réservée aux Gérants.")
        return

    staff_role = discord.utils.get(ctx.guild.roles, name=STAFF_ROLE_NAME)

    overwrite_everyone = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite_everyone.send_messages = False

    try:
        await ctx.channel.set_permissions(
            ctx.guild.default_role, overwrite=overwrite_everyone, reason=f"Salon verrouillé par {ctx.author}"
        )
        if staff_role:
            overwrite_staff = ctx.channel.overwrites_for(staff_role)
            overwrite_staff.send_messages = True
            await ctx.channel.set_permissions(
                staff_role, overwrite=overwrite_staff, reason=f"Accès staff maintenu (verrouillage par {ctx.author})"
            )
    except discord.Forbidden:
        await ctx.send("❌ Je n'ai pas la permission de modifier les permissions de ce salon.")
        return

    embed = discord.Embed(
        title="🔒 Salon verrouillé",
        description=f"Seuls les membres avec le rôle **{STAFF_ROLE_NAME}** peuvent désormais écrire ici.",
        color=discord.Color.red(),
    )
    embed.set_footer(text=f"Verrouillé par {ctx.author}")
    await ctx.send(embed=embed)


@bot.command(name="unlock")
async def unlock_command(ctx: commands.Context):
    if not is_gerant(ctx.author):
        await ctx.send("❌ Cette commande est réservée aux Gérants.")
        return

    overwrite_everyone = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite_everyone.send_messages = None  # retire l'overwrite explicite (retour à l'héritage normal)

    try:
        await ctx.channel.set_permissions(
            ctx.guild.default_role, overwrite=overwrite_everyone, reason=f"Salon déverrouillé par {ctx.author}"
        )
    except discord.Forbidden:
        await ctx.send("❌ Je n'ai pas la permission de modifier les permissions de ce salon.")
        return

    embed = discord.Embed(
        title="🔓 Salon déverrouillé",
        description="Tout le monde peut de nouveau écrire ici.",
        color=discord.Color.green(),
    )
    embed.set_footer(text=f"Déverrouillé par {ctx.author}")
    await ctx.send(embed=embed)
 
 
# ================================================================
#                        +absences
# ================================================================
 
class AbsenceModal(discord.ui.Modal, title="Déclaration d'absence"):
    pseudo = discord.ui.TextInput(label="Pseudo", placeholder="Ton pseudo Discord", max_length=100)
    date = discord.ui.TextInput(label="Date", placeholder="Ex : 20/08/2026", max_length=50)
    raison = discord.ui.TextInput(
        label="Raison",
        style=discord.TextStyle.paragraph,
        placeholder="Explique la raison de ton absence",
        max_length=500,
    )
 
    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="📋 Nouvelle déclaration d'absence",
            color=discord.Color.orange(),
            timestamp=datetime.utcnow(),
        )
        embed.add_field(name="Pseudo", value=self.pseudo.value, inline=False)
        embed.add_field(name="Date", value=self.date.value, inline=False)
        embed.add_field(name="Raison", value=self.raison.value, inline=False)
        embed.add_field(name="Statut", value="⏳ En attente de validation", inline=False)
        embed.set_footer(text=f"Envoyé par {interaction.user}", icon_url=interaction.user.display_avatar.url)
 
        await interaction.channel.send(embed=embed, view=ValidateAbsenceView(interaction.user.id))
        await interaction.response.send_message("✅ Ton absence a bien été déclarée.", ephemeral=True)
 
 
class AbsenceView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
 
    @discord.ui.button(
        label="📋 Remplir le formulaire",
        style=discord.ButtonStyle.primary,
        custom_id="absence_form_button",
    )
    async def open_form(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AbsenceModal())
 
 
class ValidateAbsenceView(discord.ui.View):
    """Bouton affiché sous chaque déclaration d'absence, réservé aux rôles autorisés."""
 
    def __init__(self, target_user_id: int):
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="✅ Valider l'absence",
                style=discord.ButtonStyle.success,
                custom_id=f"validate_absence:{target_user_id}",
            )
        )
 
 
@bot.listen("on_interaction")
async def on_validate_absence_interaction(interaction: discord.Interaction):
    """Écoute les clics sur le bouton de validation d'absence (survit aux redémarrages)."""
    if interaction.type != discord.InteractionType.component:
        return
    custom_id = interaction.data.get("custom_id", "")
    if not custom_id.startswith("validate_absence:"):
        return
 
    target_user_id = int(custom_id.split(":", 1)[1])
    allowed_role_ids = get_validator_roles(interaction.guild.id, "absences")
 
    member = interaction.user
    if allowed_role_ids:
        authorized = member.guild_permissions.administrator or any(
            role.id in allowed_role_ids for role in member.roles
        )
    else:
        authorized = is_staff(member)
 
    if not authorized:
        await interaction.response.send_message(
            "❌ Tu n'as pas la permission de valider les absences.", ephemeral=True
        )
        return
 
    target_member = interaction.guild.get_member(target_user_id)
    try:
        target_user = target_member or await bot.fetch_user(target_user_id)
        await target_user.send("Bonjour,\n\nVotre absences a été validé.")
        dm_status = "✅ Message privé envoyé à l'utilisateur."
    except discord.Forbidden:
        dm_status = "⚠️ Impossible d'envoyer le message privé (DMs fermés)."
    except discord.HTTPException:
        dm_status = "⚠️ Erreur lors de l'envoi du message privé."
 
    embed = interaction.message.embeds[0]
    embed.color = discord.Color.green()
    if embed.fields and embed.fields[-1].name == "Statut":
        embed.set_field_at(len(embed.fields) - 1, name="Statut", value=f"✅ Validée par {interaction.user.mention}", inline=False)
    else:
        embed.add_field(name="Statut", value=f"✅ Validée par {interaction.user.mention}", inline=False)
 
    await interaction.message.edit(embed=embed, view=None)
    await interaction.response.send_message(f"Absence validée. {dm_status}", ephemeral=True)
 
 
@bot.command(name="absences")
async def absences_command(ctx: commands.Context):
    if not is_staff(ctx.author):
        await ctx.send("❌ Cette commande est réservée au staff.")
        return
 
    embed = discord.Embed(
        title="📋 Déclaration d'absence",
        description="Clique sur le bouton ci-dessous pour remplir le formulaire d'absence.",
        color=discord.Color.orange(),
    )
    await ctx.send(embed=embed, view=AbsenceView())
 
 
# ================================================================
#                    +role-react setup
# ================================================================
 
def parse_role_from_text(guild: discord.Guild, text: str) -> discord.Role | None:
    text = text.strip()
    if text.startswith("<@&") and text.endswith(">"):
        try:
            role_id = int(text[3:-1])
        except ValueError:
            return None
        return guild.get_role(role_id)
    return discord.utils.get(guild.roles, name=text)
 
 
@bot.command(name="role-react")
async def role_react_command(ctx: commands.Context, sous_commande: str = None):
    if sous_commande is None or sous_commande.lower() != "setup":
        await ctx.send("Utilise `+role-react setup` pour créer un message à réactions.")
        return
 
    if not is_staff(ctx.author):
        await ctx.send("❌ Cette commande est réservée au staff.")
        return
 
    def check(m: discord.Message) -> bool:
        return m.author.id == ctx.author.id and m.channel.id == ctx.channel.id
 
    try:
        await ctx.send("📝 Envoie le **titre** du message (ou `annuler` pour arrêter).")
        msg_title = await bot.wait_for("message", check=check, timeout=120)
        if msg_title.content.lower() == "annuler":
            await ctx.send("❌ Configuration annulée.")
            return
        titre = msg_title.content
 
        await ctx.send("📝 Envoie maintenant la **description** du message.")
        msg_desc = await bot.wait_for("message", check=check, timeout=120)
        if msg_desc.content.lower() == "annuler":
            await ctx.send("❌ Configuration annulée.")
            return
        description = msg_desc.content
 
        await ctx.send(
            "📝 Envoie maintenant la liste **emoji + rôle**, une paire par ligne.\n"
            "Exemple :\n🔴 @Rouge\n🔵 @Bleu\n\n"
            "(emojis Discord classiques ou personnalisés du serveur, rôles en mention `@rôle`)"
        )
        msg_pairs = await bot.wait_for("message", check=check, timeout=180)
        if msg_pairs.content.lower() == "annuler":
            await ctx.send("❌ Configuration annulée.")
            return
 
        pairs = {}
        lines_summary = []
        for line in msg_pairs.content.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(maxsplit=1)
            if len(parts) != 2:
                continue
            emoji_str, role_part = parts
            role = parse_role_from_text(ctx.guild, role_part)
            if role is None:
                continue
            pairs[emoji_str] = role.id
            lines_summary.append(f"{emoji_str} → {role.mention}")
 
        if not pairs:
            await ctx.send("❌ Aucune paire emoji/rôle valide détectée. Recommence avec `+role-react setup`.")
            return
 
        embed = discord.Embed(title=titre, description=description, color=discord.Color.blue())
        embed.add_field(name="Rôles disponibles", value="\n".join(lines_summary), inline=False)
        embed.set_footer(text="Réagis avec l'emoji correspondant pour obtenir le rôle.")
 
        role_message = await ctx.send(embed=embed)
 
        for emoji_str in pairs:
            try:
                await role_message.add_reaction(emoji_str)
            except discord.HTTPException:
                pass
 
        guild_conf = config.setdefault(str(ctx.guild.id), {})
        role_react_conf = guild_conf.setdefault("role_react", {})
        role_react_conf[str(role_message.id)] = pairs
        save_config(config)
 
        await ctx.send("✅ Le message à réactions a été créé avec succès !")
 
    except asyncio.TimeoutError:
        await ctx.send("⏱️ Temps écoulé, configuration annulée.")
 
 
@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.guild_id is None or payload.member is None or payload.member.bot:
        return
 
    mapping = config.get(str(payload.guild_id), {}).get("role_react", {}).get(str(payload.message_id))
    if not mapping:
        return
 
    role_id = mapping.get(str(payload.emoji))
    if role_id is None:
        return
 
    guild = bot.get_guild(payload.guild_id)
    role = guild.get_role(role_id) if guild else None
    if role:
        try:
            await payload.member.add_roles(role, reason="Role-react")
        except discord.HTTPException:
            pass
 
 
@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    if payload.guild_id is None:
        return
 
    mapping = config.get(str(payload.guild_id), {}).get("role_react", {}).get(str(payload.message_id))
    if not mapping:
        return
 
    role_id = mapping.get(str(payload.emoji))
    if role_id is None:
        return
 
    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return
    member = guild.get_member(payload.user_id)
    if member is None or member.bot:
        return
 
    role = guild.get_role(role_id)
    if role:
        try:
            await member.remove_roles(role, reason="Role-react retiré")
        except discord.HTTPException:
            pass
 
 
# ================================================================
#                    /ticketsetup — SYSTÈME DE TICKETS
# ================================================================
#
# +ticketsetup ouvre une fenêtre (modal) qui permet de tout personnaliser
# en une seule fois : titre de l'embed, texte au-dessus, texte en dessous,
# et la liste des boutons (label, emoji, couleur), exactement comme dans
# le screenshot fourni (plusieurs boutons de couleurs différentes qui
# ouvrent chacun un salon de ticket privé).
#
# Format d'une ligne de bouton dans le modal :
#   Label | emoji (optionnel) | couleur (optionnel)
# Couleurs acceptées : blurple/primary/bleu, green/vert/success,
#                      grey/gray/gris/secondary, red/rouge/danger
#
# Exemple :
#   Porter Plainte | 📩 | blurple
#   Contacter le Corps des Officiers | 📩 | green
#   Contacter les Brigades Spéciales | 📩 | grey
 
TICKET_STYLE_MAP = {
    "blurple": discord.ButtonStyle.primary,
    "primary": discord.ButtonStyle.primary,
    "bleu": discord.ButtonStyle.primary,
    "blue": discord.ButtonStyle.primary,
    "green": discord.ButtonStyle.success,
    "vert": discord.ButtonStyle.success,
    "success": discord.ButtonStyle.success,
    "grey": discord.ButtonStyle.secondary,
    "gray": discord.ButtonStyle.secondary,
    "gris": discord.ButtonStyle.secondary,
    "secondary": discord.ButtonStyle.secondary,
    "red": discord.ButtonStyle.danger,
    "rouge": discord.ButtonStyle.danger,
    "danger": discord.ButtonStyle.danger,
}
 
 
def get_ticket_panels(guild_id: int) -> dict:
    return config.get(str(guild_id), {}).get("ticket_panels", {})
 
 
def save_ticket_panel(guild_id: int, panel_id: str, buttons_data: list, category_name: str, ping_role_ids: list | None = None) -> None:
    guild_conf = config.setdefault(str(guild_id), {})
    ticket_panels = guild_conf.setdefault("ticket_panels", {})
    ticket_panels[panel_id] = {
        "buttons": buttons_data,
        "category_name": category_name,
        "ping_role_ids": ping_role_ids or [],
    }
    save_config(config)
 
 
class TicketButton(discord.ui.Button):
    """Bouton dynamique de panneau de tickets. Le custom_id encode panel_id et index
    pour retrouver la configuration du bouton, même après un redémarrage du bot."""
 
    async def callback(self, interaction: discord.Interaction):
        await handle_ticket_open(interaction, self.custom_id)
 
 
def build_ticket_panel_view(panel_id: str, buttons_data: list) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for idx, data in enumerate(buttons_data):
        style = getattr(discord.ButtonStyle, data.get("style", "primary"), discord.ButtonStyle.primary)
        emoji = data.get("emoji") or None
        view.add_item(
            TicketButton(
                label=data["label"],
                emoji=emoji,
                style=style,
                custom_id=f"ticket_open:{panel_id}:{idx}",
            )
        )
    return view
 
 
async def handle_ticket_open(interaction: discord.Interaction, custom_id: str) -> None:
    try:
        _, panel_id, idx_str = custom_id.split(":", 2)
        idx = int(idx_str)
    except ValueError:
        return
 
    guild = interaction.guild
    if guild is None:
        return
 
    guild_conf = config.setdefault(str(guild.id), {})
    panel_conf = guild_conf.get("ticket_panels", {}).get(panel_id)
    if not panel_conf or idx >= len(panel_conf["buttons"]):
        await interaction.response.send_message("❌ Ce panneau de tickets n'est plus valide.", ephemeral=True)
        return
 
    bouton_conf = panel_conf["buttons"][idx]
    label = bouton_conf["label"]
 
    tickets_open = guild_conf.setdefault("tickets_open", {})
    open_key = f"{interaction.user.id}:{panel_id}:{idx}"
    existing_channel_id = tickets_open.get(open_key)
    if existing_channel_id:
        existing_channel = guild.get_channel(existing_channel_id)
        if existing_channel:
            await interaction.response.send_message(
                f"⚠️ Tu as déjà un ticket ouvert pour **{label}** : {existing_channel.mention}", ephemeral=True
            )
            return
        del tickets_open[open_key]
 
    await interaction.response.defer(ephemeral=True)
 
    category_name = panel_conf.get("category_name") or TICKETS_CATEGORY_NAME
    category = discord.utils.get(guild.categories, name=category_name)
    if category is None:
        try:
            category = await guild.create_category(category_name)
        except discord.Forbidden:
            await interaction.followup.send("❌ Je n'ai pas la permission de créer la catégorie de tickets.", ephemeral=True)
            return
 
    staff_role = discord.utils.get(guild.roles, name=STAFF_ROLE_NAME)
    ping_role_ids = panel_conf.get("ping_role_ids") or []
    ping_roles = [r for r in (guild.get_role(rid) for rid in ping_role_ids) if r is not None]

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True),
    }
    if staff_role:
        overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
    for role in ping_roles:
        overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
 
    slug = "".join(c if c.isalnum() else "-" for c in label.lower()).strip("-")[:40] or "ticket"
    channel_name = f"ticket-{slug}-{interaction.user.name}".lower()[:90]
 
    try:
        ticket_channel = await guild.create_text_channel(
            channel_name,
            category=category,
            overwrites=overwrites,
            topic=f"ticket_owner:{interaction.user.id}",
            reason=f"Ticket ouvert par {interaction.user} ({label})",
        )
    except discord.Forbidden:
        await interaction.followup.send("❌ Je n'ai pas la permission de créer un salon de ticket.", ephemeral=True)
        return
    except discord.HTTPException:
        await interaction.followup.send("❌ Erreur lors de la création du salon de ticket.", ephemeral=True)
        return
 
    tickets_open[open_key] = ticket_channel.id
    save_config(config)
 
    ticket_embed = discord.Embed(
        title=f"🎫 {label}",
        description=(
            f"Bonjour {interaction.user.mention}, merci d'avoir ouvert un ticket.\n\n"
            "Explique ta demande en détail ci-dessous, un membre du staff te répondra dès que possible."
        ),
        color=discord.Color.blurple(),
    )
    if ping_roles:
        mention_text = " ".join(role.mention for role in ping_roles)
    elif staff_role:
        mention_text = staff_role.mention
    else:
        mention_text = ""
    await ticket_channel.send(content=f"{interaction.user.mention} {mention_text}".strip(), embed=ticket_embed, view=TicketCloseView())
 
    await interaction.followup.send(f"✅ Ton ticket a été créé : {ticket_channel.mention}", ephemeral=True)
 
 
class TicketCloseView(discord.ui.View):
    """Vue statique (custom_id fixe) affichée dans chaque salon de ticket pour le fermer."""
 
    def __init__(self):
        super().__init__(timeout=None)
 
    @discord.ui.button(label="🔒 Fermer le ticket", style=discord.ButtonStyle.danger, custom_id="ticket_close")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        topic = channel.topic or ""
        owner_id = None
        if topic.startswith("ticket_owner:"):
            try:
                owner_id = int(topic.split(":", 1)[1])
            except ValueError:
                owner_id = None
 
        if not (is_staff(interaction.user) or interaction.user.id == owner_id):
            await interaction.response.send_message("❌ Tu ne peux pas fermer ce ticket.", ephemeral=True)
            return
 
        await interaction.response.send_message(f"🔒 Ticket fermé par {interaction.user.mention}. Suppression dans 5 secondes...")
 
        guild_conf = config.get(str(interaction.guild.id), {})
        tickets_open = guild_conf.get("tickets_open", {})
        key_to_remove = next((k for k, v in tickets_open.items() if v == channel.id), None)
        if key_to_remove:
            del tickets_open[key_to_remove]
            save_config(config)
 
        await asyncio.sleep(5)
        try:
            await channel.delete(reason=f"Ticket fermé par {interaction.user}")
        except discord.HTTPException:
            pass
 
 
class TicketSetupModal(discord.ui.Modal, title="Configuration du panneau de tickets"):
    titre = discord.ui.TextInput(
        label="Titre de l'embed",
        placeholder="Ex : 📩 Centre d'assistance",
        max_length=256,
    )
    texte_haut = discord.ui.TextInput(
        label="Texte au-dessus des boutons",
        style=discord.TextStyle.paragraph,
        placeholder="Explique le fonctionnement du système de tickets...",
        max_length=1000,
    )
    texte_bas = discord.ui.TextInput(
        label="Texte en dessous (optionnel)",
        style=discord.TextStyle.paragraph,
        placeholder="Infos complémentaires, règles, horaires de réponse...",
        required=False,
        max_length=1000,
    )
    boutons = discord.ui.TextInput(
        label="Boutons : Label | emoji | couleur",
        style=discord.TextStyle.paragraph,
        placeholder="Porter Plainte | 📩 | blurple\nAutre Contact | 📩 | green",
        max_length=1000,
    )
    categorie_nom = discord.ui.TextInput(
        label="Nom de la catégorie tickets (optionnel)",
        placeholder=f"Par défaut : {TICKETS_CATEGORY_NAME}",
        required=False,
        max_length=100,
    )
 
    async def on_submit(self, interaction: discord.Interaction):
        buttons_data = []
        for line in self.boutons.value.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split("|")]
            label = parts[0] if parts else ""
            if not label:
                continue
            emoji = parts[1] if len(parts) >= 2 and parts[1] else None
            style_key = parts[2].lower() if len(parts) >= 3 and parts[2] else "primary"
            style_enum = TICKET_STYLE_MAP.get(style_key, discord.ButtonStyle.primary)
            buttons_data.append({"label": label[:80], "emoji": emoji, "style": style_enum.name})
 
        if not buttons_data:
            await interaction.response.send_message(
                "❌ Aucun bouton valide détecté. Format attendu : `Label | emoji | couleur` (un par ligne).",
                ephemeral=True,
            )
            return
 
        if len(buttons_data) > 20:
            buttons_data = buttons_data[:20]
 
        category_name = self.categorie_nom.value.strip() or TICKETS_CATEGORY_NAME
        embed_data = {
            "titre": self.titre.value,
            "texte_haut": self.texte_haut.value,
            "texte_bas": self.texte_bas.value,
        }
 
        # Discord n'autorise pas les menus déroulants dans une fenêtre (modal) —
        # uniquement des champs texte. Dernière étape, juste après : un menu
        # déroulant natif listant tous les rôles du serveur (scrollable) pour
        # choisir qui sera ping à l'ouverture d'un ticket.
        await interaction.response.send_message(
            "🔧 Dernière étape : choisis le(s) rôle(s) à ping quand un ticket est ouvert, "
            "ou clique sur **Passer** pour ne ping personne.",
            view=TicketRoleSelectView(buttons_data, category_name, embed_data),
            ephemeral=True,
        )
 
 
class TicketRoleSelectView(discord.ui.View):
    """Étape finale de /ticketsetup : menu déroulant natif listant tous les rôles
    du serveur (scrollable), pour choisir qui est ping à l'ouverture d'un ticket."""

    def __init__(self, buttons_data: list, category_name: str, embed_data: dict):
        super().__init__(timeout=300)
        self.buttons_data = buttons_data
        self.category_name = category_name
        self.embed_data = embed_data
        self._done = False

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="Rôle(s) à ping à l'ouverture d'un ticket (optionnel)",
        min_values=0,
        max_values=5,
    )
    async def role_select(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        role_ids = [role.id for role in select.values]
        await self._finalize(interaction, role_ids)

    @discord.ui.button(label="Passer (aucun ping)", style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._finalize(interaction, [])

    async def _finalize(self, interaction: discord.Interaction, role_ids: list):
        if self._done:
            return
        self._done = True

        panel_id = uuid.uuid4().hex
        save_ticket_panel(interaction.guild.id, panel_id, self.buttons_data, self.category_name, role_ids)

        embed = discord.Embed(
            title=self.embed_data["titre"],
            description=self.embed_data["texte_haut"],
            color=discord.Color.from_rgb(20, 20, 24),
        )
        if self.embed_data["texte_bas"]:
            embed.add_field(name="\u200b", value=self.embed_data["texte_bas"], inline=False)

        panel_view = build_ticket_panel_view(panel_id, self.buttons_data)

        self.stop()
        await interaction.response.edit_message(
            content="✅ Panneau de tickets configuré et envoyé ci-dessous !", view=None
        )
        await interaction.channel.send(embed=embed, view=panel_view)
 
 
@bot.tree.command(name="ticketsetup", description="[Staff] Crée un panneau de tickets personnalisable")
async def ticketsetup(interaction: discord.Interaction):
    if not is_staff(interaction.user):
        await interaction.response.send_message(
            "❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True
        )
        return
    await interaction.response.send_modal(TicketSetupModal())
 
 
# ================================================================
#          SYSTÈME D'ANNIVERSAIRES (optionnel, /anniv config)
# ================================================================
#
# Le système est désactivé par défaut sur un serveur : il faut que le staff
# fasse /anniv config pour choisir le salon d'annonce (et éventuellement
# personnaliser le message). Une fois configuré, les membres peuvent utiliser
# /anniversaire create pour enregistrer leur date (jour + mois uniquement,
# jamais l'année — donc pas de date de naissance ni de calcul d'âge).
# Chaque membre ne peut enregistrer qu'un seul anniversaire (utiliser
# /anniversaire modifier pour le changer).

ANNIV_CHECK_TIME = dt_time(hour=9, minute=0, tzinfo=PARIS_TZ)  # Heure de vérification quotidienne
ANNIV_DEFAULT_MESSAGE = "🎉🎂 Joyeux anniversaire {membre} !"


def is_anniv_enabled(guild_id: int) -> bool:
    return bool(config.get(str(guild_id), {}).get("anniv_config", {}).get("channel_id"))


def parse_anniv_date(date_str: str):
    """Parse une date au format JJ/MM (l'année, si fournie, est ignorée).
    Retourne (jour, mois) ou None si invalide."""
    parts = [p for p in re.split(r"[\/\-\. ]+", date_str.strip()) if p]
    if len(parts) < 2:
        return None
    try:
        jour = int(parts[0])
        mois = int(parts[1])
    except ValueError:
        return None

    if not (1 <= mois <= 12):
        return None
    # 2024 est bissextile : autorise le 29 février dans tous les cas.
    max_jour = calendar.monthrange(2024, mois)[1]
    if not (1 <= jour <= max_jour):
        return None

    return jour, mois


@tasks.loop(time=ANNIV_CHECK_TIME)
async def check_anniversaires():
    now = datetime.now(PARIS_TZ)
    for guild in bot.guilds:
        guild_conf = config.get(str(guild.id), {})
        anniv_conf = guild_conf.get("anniv_config")
        if not anniv_conf or not anniv_conf.get("channel_id"):
            continue

        channel = guild.get_channel(anniv_conf["channel_id"])
        if channel is None:
            continue

        message_template = anniv_conf.get("message") or ANNIV_DEFAULT_MESSAGE
        birthdays = guild_conf.get("birthdays", {})

        for user_id, bday in birthdays.items():
            if bday.get("day") == now.day and bday.get("month") == now.month:
                member = guild.get_member(int(user_id))
                if member is None:
                    continue
                texte = message_template.replace("{membre}", member.mention)
                try:
                    await channel.send(texte)
                except discord.HTTPException:
                    pass


@check_anniversaires.before_loop
async def before_check_anniversaires():
    await bot.wait_until_ready()


anniv_group = app_commands.Group(name="anniv", description="Configuration du système d'anniversaires (staff)")


@anniv_group.command(name="config", description="[Staff] Active/configure le système d'anniversaires")
@app_commands.describe(
    salon="Salon où seront annoncés les anniversaires",
    message="Message personnalisé (utilise {membre} pour mentionner la personne)",
)
async def anniv_config_cmd(interaction: discord.Interaction, salon: discord.TextChannel, message: str = None):
    if not is_staff(interaction.user):
        await interaction.response.send_message(
            "❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True
        )
        return

    guild_conf = config.setdefault(str(interaction.guild.id), {})
    anniv_conf = guild_conf.setdefault("anniv_config", {})
    anniv_conf["channel_id"] = salon.id
    if message:
        anniv_conf["message"] = message
    save_config(config)

    await interaction.response.send_message(
        f"✅ Système d'anniversaires activé ! Les anniversaires seront annoncés dans {salon.mention} "
        "chaque jour à 9h (heure de Paris).\n"
        "Les membres peuvent maintenant utiliser `/anniversaire create`.",
        ephemeral=True,
    )


@anniv_group.command(name="desactiver", description="[Staff] Désactive le système d'anniversaires")
async def anniv_desactiver_cmd(interaction: discord.Interaction):
    if not is_staff(interaction.user):
        await interaction.response.send_message(
            "❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True
        )
        return

    guild_conf = config.setdefault(str(interaction.guild.id), {})
    if "anniv_config" in guild_conf:
        del guild_conf["anniv_config"]
        save_config(config)

    await interaction.response.send_message("✅ Système d'anniversaires désactivé sur ce serveur.", ephemeral=True)


bot.tree.add_command(anniv_group)


anniversaire_group = app_commands.Group(name="anniversaire", description="Gère ton anniversaire (jour/mois uniquement)")


@anniversaire_group.command(name="create", description="Enregistre ton anniversaire (jour/mois uniquement)")
@app_commands.describe(date="Date de ton anniversaire au format JJ/MM (ex : 25/12). Aucune année demandée.")
async def anniversaire_create_cmd(interaction: discord.Interaction, date: str):
    if not is_anniv_enabled(interaction.guild.id):
        await interaction.response.send_message(
            "❌ Le système d'anniversaires n'est pas activé sur ce serveur.", ephemeral=True
        )
        return

    parsed = parse_anniv_date(date)
    if parsed is None:
        await interaction.response.send_message(
            "❌ Format de date invalide. Utilise `JJ/MM`, par exemple `25/12`.", ephemeral=True
        )
        return
    jour, mois = parsed

    guild_conf = config.setdefault(str(interaction.guild.id), {})
    birthdays = guild_conf.setdefault("birthdays", {})
    uid = str(interaction.user.id)

    if uid in birthdays:
        await interaction.response.send_message(
            "⚠️ Tu as déjà enregistré un anniversaire. Utilise `/anniversaire modifier` pour le changer.",
            ephemeral=True,
        )
        return

    birthdays[uid] = {"day": jour, "month": mois}
    save_config(config)

    await interaction.response.send_message(
        f"✅ Ton anniversaire ({jour:02d}/{mois:02d}) a bien été enregistré 🎉 (aucune année n'est demandée ni stockée).",
        ephemeral=True,
    )


@anniversaire_group.command(name="modifier", description="Modifie la date de ton anniversaire déjà enregistré")
@app_commands.describe(date="Nouvelle date au format JJ/MM (ex : 25/12)")
async def anniversaire_modifier_cmd(interaction: discord.Interaction, date: str):
    if not is_anniv_enabled(interaction.guild.id):
        await interaction.response.send_message(
            "❌ Le système d'anniversaires n'est pas activé sur ce serveur.", ephemeral=True
        )
        return

    parsed = parse_anniv_date(date)
    if parsed is None:
        await interaction.response.send_message(
            "❌ Format de date invalide. Utilise `JJ/MM`, par exemple `25/12`.", ephemeral=True
        )
        return
    jour, mois = parsed

    guild_conf = config.setdefault(str(interaction.guild.id), {})
    birthdays = guild_conf.setdefault("birthdays", {})
    uid = str(interaction.user.id)

    if uid not in birthdays:
        await interaction.response.send_message(
            "❌ Tu n'as pas encore d'anniversaire enregistré. Utilise `/anniversaire create`.", ephemeral=True
        )
        return

    birthdays[uid] = {"day": jour, "month": mois}
    save_config(config)

    await interaction.response.send_message(f"✅ Ton anniversaire a été mis à jour : {jour:02d}/{mois:02d}.", ephemeral=True)


@anniversaire_group.command(name="supprimer", description="Supprime ton anniversaire enregistré")
async def anniversaire_supprimer_cmd(interaction: discord.Interaction):
    guild_conf = config.setdefault(str(interaction.guild.id), {})
    birthdays = guild_conf.setdefault("birthdays", {})
    uid = str(interaction.user.id)

    if uid not in birthdays:
        await interaction.response.send_message("❌ Tu n'as pas d'anniversaire enregistré.", ephemeral=True)
        return

    del birthdays[uid]
    save_config(config)
    await interaction.response.send_message("✅ Ton anniversaire a été supprimé.", ephemeral=True)


@anniversaire_group.command(name="liste", description="Affiche les prochains anniversaires du serveur")
async def anniversaire_liste_cmd(interaction: discord.Interaction):
    guild_conf = config.get(str(interaction.guild.id), {})
    birthdays = guild_conf.get("birthdays", {})

    if not birthdays:
        await interaction.response.send_message("Aucun anniversaire enregistré pour le moment.", ephemeral=True)
        return

    today = datetime.now(PARIS_TZ).date()

    def prochaine_occurrence(jour: int, mois: int):
        annee = today.year
        try:
            d = datetime(annee, mois, jour, tzinfo=PARIS_TZ).date()
        except ValueError:
            d = datetime(annee, 3, 1, tzinfo=PARIS_TZ).date()
        if d < today:
            try:
                d = datetime(annee + 1, mois, jour, tzinfo=PARIS_TZ).date()
            except ValueError:
                d = datetime(annee + 1, 3, 1, tzinfo=PARIS_TZ).date()
        return d

    entries = []
    for uid, bday in birthdays.items():
        prochaine = prochaine_occurrence(bday["day"], bday["month"])
        entries.append((prochaine, uid, bday))
    entries.sort(key=lambda e: e[0])

    lignes = []
    for prochaine, uid, bday in entries[:15]:
        member = interaction.guild.get_member(int(uid))
        nom = member.mention if member else f"<@{uid}>"
        date_str = f"{bday['day']:02d}/{bday['month']:02d}"
        delta = (prochaine - today).days
        suffix = "🎉 **Aujourd'hui !**" if delta == 0 else f"dans {delta} jour(s)"
        lignes.append(f"• {nom} — {date_str} ({suffix})")

    embed = discord.Embed(
        title="🎂 Prochains anniversaires",
        description="\n".join(lignes),
        color=discord.Color.pink(),
    )
    await interaction.response.send_message(embed=embed)


bot.tree.add_command(anniversaire_group)
 
 
# ================================================================
#          NOTIFICATIONS TIKTOK (/config tiktok)
# ================================================================
#
# ⚠️ TikTok ne propose pas d'API publique officielle permettant de surveiller
# les nouvelles vidéos d'un compte arbitraire. La méthode ci-dessous analyse
# le HTML public de la page de profil (aucune connexion ni identifiant TikTok
# requis) pour retrouver la dernière vidéo publiée. TikTok modifie
# régulièrement la structure de ses pages et peut bloquer les requêtes
# automatisées : cette fonctionnalité est donc fournie en best-effort et peut
# nécessiter une maintenance si TikTok change son site.

async def fetch_latest_tiktok_video(username: str):
    """Récupère {id, url, description} de la dernière vidéo publique du compte,
    ou None si indisponible/erreur."""
    url = f"https://www.tiktok.com/@{username}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text()
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None

    match = re.search(
        r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not match:
        return None

    try:
        data = json.loads(match.group(1))
        item_list = data["__DEFAULT_SCOPE__"]["webapp.user-detail"]["userInfo"]["itemList"]
    except (KeyError, TypeError, json.JSONDecodeError):
        return None

    if not item_list:
        return None

    latest = max(item_list, key=lambda it: int(it.get("createTime", 0) or 0))
    video_id = latest.get("id")
    if not video_id:
        return None

    return {
        "id": str(video_id),
        "url": f"https://www.tiktok.com/@{username}/video/{video_id}",
        "description": latest.get("desc", ""),
    }


@tasks.loop(minutes=TIKTOK_CHECK_INTERVAL_MINUTES)
async def check_tiktok_loop():
    video = await fetch_latest_tiktok_video(TIKTOK_USERNAME)
    if not video:
        return

    last_id = config.get("tiktok_last_video_id")
    if last_id is None:
        # Premier lancement : on mémorise la vidéo actuelle sans notifier,
        # pour ne pas spammer avec d'anciennes vidéos.
        config["tiktok_last_video_id"] = video["id"]
        save_config(config)
        return

    if video["id"] == last_id:
        return

    config["tiktok_last_video_id"] = video["id"]
    save_config(config)

    for guild in bot.guilds:
        guild_conf = config.get(str(guild.id), {})
        tiktok_conf = guild_conf.get("tiktok_config")
        if not tiktok_conf or not tiktok_conf.get("channel_id") or not tiktok_conf.get("actif", True):
            continue

        channel = guild.get_channel(tiktok_conf["channel_id"])
        if channel is None:
            continue

        template = tiktok_conf.get("message") or (
            f"📱 Nouvelle vidéo TikTok de **@{TIKTOK_USERNAME}** !\n{{lien}}"
        )
        texte = template.replace("{lien}", video["url"]).replace("{compte}", f"@{TIKTOK_USERNAME}")
        try:
            await channel.send(texte)
        except discord.HTTPException:
            pass


@check_tiktok_loop.before_loop
async def before_check_tiktok_loop():
    await bot.wait_until_ready()


config_group = app_commands.Group(name="config", description="Commandes de configuration du bot")


@config_group.command(name="tiktok", description=f"[Staff] Configure les notifications de nouvelles vidéos de @{TIKTOK_USERNAME}")
@app_commands.describe(
    salon="Salon où seront envoyées les notifications de nouvelles vidéos",
    message="Message personnalisé (utilise {lien} pour le lien de la vidéo et {compte} pour le nom du compte)",
    actif="Active ou désactive les notifications (activé par défaut)",
)
async def config_tiktok_cmd(
    interaction: discord.Interaction,
    salon: discord.TextChannel,
    message: str = None,
    actif: bool = True,
):
    if not is_staff(interaction.user):
        await interaction.response.send_message(
            "❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True
        )
        return

    guild_conf = config.setdefault(str(interaction.guild.id), {})
    tiktok_conf = guild_conf.setdefault("tiktok_config", {})
    tiktok_conf["channel_id"] = salon.id
    tiktok_conf["actif"] = actif
    if message:
        tiktok_conf["message"] = message
    save_config(config)

    etat = "activées ✅" if actif else "désactivées ⏸️"
    await interaction.response.send_message(
        f"✅ Notifications TikTok pour **@{TIKTOK_USERNAME}** configurées sur {salon.mention} ({etat}).",
        ephemeral=True,
    )


bot.tree.add_command(config_group)
 
 
# ================================================================
#      SYSTÈME D'ANIMAUX À CAPTURER (optionnel, /animal config)
# ================================================================
#
# Chaque jour, à une heure aléatoire, un animal sauvage apparaît dans le
# salon configuré. Le premier membre à cliquer sur "Capturer !" le remporte.
# La rareté de l'animal qui apparaît est tirée au sort selon les pourcentages
# ci-dessous, puis un animal est choisi au hasard parmi ceux de cette rareté.

ANIMAUX = [
    {"nom": "Yuzenn", "rarete": "Owner"},
    {"nom": "Snow", "rarete": "Owner"},
    {"nom": "Rebeu", "rarete": "Co-Owner"},
    {"nom": "Cafard", "rarete": "Modérateur"},
    {"nom": "R0tten", "rarete": "VIP"},
    {"nom": "Lgz", "rarete": "VIP"},
    {"nom": "9z_wl", "rarete": "Membre"},
    {"nom": "Beurre2KKhouette", "rarete": "Membre"},
    {"nom": "Slayzxx", "rarete": "Membre"},
]

RARETE_WEIGHTS = {
    "Owner": 0.5,
    "Co-Owner": 2,
    "Modérateur": 10,
    "VIP": 28,
    "Membre": 59.5,
}

RARETE_COLORS = {
    "Owner": discord.Color.red(),
    "Co-Owner": discord.Color.orange(),
    "Modérateur": discord.Color.purple(),
    "VIP": discord.Color.gold(),
    "Membre": discord.Color.light_grey(),
}

ANIMAL_SPAWN_HOUR_MIN = 8       # Heure la plus tôt possible pour un spawn (heure de Paris)
ANIMAL_SPAWN_HOUR_MAX = 23      # Heure la plus tardive possible pour un spawn
ANIMAL_DESPAWN_SECONDS = 300    # Temps disponible pour capturer l'animal (5 minutes) avant qu'il ne s'enfuie
ANIMAL_CHECK_INTERVAL_MINUTES = 1

# Boost de chance temporaire (déclenché depuis /admin panel). En mémoire
# uniquement : {guild_id: {"multiplier": float, "expires_at": datetime}}
LUCK_BOOST: dict[int, dict] = {}


def get_active_luck_multiplier(guild_id: int) -> float:
    boost = LUCK_BOOST.get(guild_id)
    if not boost:
        return 1.0
    if datetime.now(PARIS_TZ) >= boost["expires_at"]:
        del LUCK_BOOST[guild_id]
        return 1.0
    return boost["multiplier"]


def pick_random_animal(guild_id: int | None = None) -> dict:
    """Tire une rareté selon les pourcentages configurés, puis un animal
    au hasard parmi ceux de cette rareté. Si un boost de chance est actif sur
    le serveur, les raretés autres que 'Membre' voient leur poids multiplié."""
    raretes = list(RARETE_WEIGHTS.keys())
    poids = list(RARETE_WEIGHTS.values())

    if guild_id is not None:
        multiplicateur = get_active_luck_multiplier(guild_id)
        if multiplicateur != 1.0:
            poids = [
                p if rarete == "Membre" else p * multiplicateur
                for rarete, p in zip(raretes, poids)
            ]

    rarete_choisie = random.choices(raretes, weights=poids, k=1)[0]
    candidats = [a for a in ANIMAUX if a["rarete"] == rarete_choisie]
    return random.choice(candidats)


def compute_next_spawn_datetime(base: datetime) -> datetime:
    """Calcule une heure aléatoire du jour suivant `base`, entre
    ANIMAL_SPAWN_HOUR_MIN et ANIMAL_SPAWN_HOUR_MAX (heure de Paris)."""
    heure = random.randint(ANIMAL_SPAWN_HOUR_MIN, ANIMAL_SPAWN_HOUR_MAX)
    minute = random.randint(0, 59)
    prochain_jour = base + timedelta(days=1)
    return prochain_jour.replace(hour=heure, minute=minute, second=0, microsecond=0)


def build_animal_spawn_embed(animal: dict) -> discord.Embed:
    color = RARETE_COLORS.get(animal["rarete"], discord.Color.blurple())
    embed = discord.Embed(
        title="🐾 Un animal sauvage est apparu !",
        description=f"Un **{animal['nom']}** rôde dans les parages...\nSois le premier à cliquer pour le capturer !",
        color=color,
    )
    embed.add_field(name="🐾 Espèce", value=animal["rarete"], inline=True)
    embed.add_field(name="⭐ Rareté", value=animal["rarete"], inline=True)
    embed.set_footer(text="Ce compagnon sauvage attend un maître...")
    return embed


def build_animal_captured_embed(animal: dict, user: discord.abc.User) -> discord.Embed:
    color = RARETE_COLORS.get(animal["rarete"], discord.Color.green())
    embed = discord.Embed(
        title=f"{animal['nom']} — Capturé !",
        description=f"{user.mention} a capturé **{animal['nom']}** !",
        color=color,
    )
    embed.add_field(name="🐾 Espèce", value=animal["rarete"], inline=True)
    embed.add_field(name="⭐ Rareté", value=animal["rarete"], inline=True)
    date_str = datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M")
    embed.set_footer(text=f"Ce compagnon a trouvé un maître. • {date_str}")
    return embed


def build_animal_escaped_embed(animal: dict) -> discord.Embed:
    embed = discord.Embed(
        title=f"{animal['nom']} — Enfui !",
        description=f"Personne n'a capturé **{animal['nom']}** à temps... il s'est enfui. 😢",
        color=discord.Color.dark_grey(),
    )
    embed.add_field(name="🐾 Espèce", value=animal["rarete"], inline=True)
    embed.add_field(name="⭐ Rareté", value=animal["rarete"], inline=True)
    return embed


class AnimalCaptureView(discord.ui.View):
    """Vue temporaire (non persistante) affichée sous un animal sauvage.
    Le premier clic sur le bouton remporte l'animal."""

    def __init__(self, animal: dict, guild_id: int):
        super().__init__(timeout=ANIMAL_DESPAWN_SECONDS)
        self.animal = animal
        self.guild_id = guild_id
        self.captured = False
        self.message: discord.Message | None = None

    @discord.ui.button(label="🎯 Capturer !", style=discord.ButtonStyle.primary)
    async def capturer(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.captured:
            await interaction.response.send_message(
                "😢 Trop tard, quelqu'un d'autre l'a déjà capturé !", ephemeral=True
            )
            return

        # Verrouillage immédiat (avant tout await) pour éviter qu'un double-clic
        # simultané ne fasse gagner l'animal à deux personnes à la fois.
        self.captured = True

        guild_conf = config.setdefault(str(self.guild_id), {})
        collections = guild_conf.setdefault("animal_collections", {})
        user_animaux = collections.setdefault(str(interaction.user.id), [])
        user_animaux.append(self.animal["nom"])
        save_config(config)

        embed = build_animal_captured_embed(self.animal, interaction.user)
        button.style = discord.ButtonStyle.success
        button.label = "✅ Capturé !"
        button.disabled = True
        self.stop()
        await interaction.response.edit_message(embed=embed, view=self)

        if interaction.guild is not None:
            try:
                await check_and_complete_quetes(interaction.guild)
            except discord.HTTPException:
                pass

    async def on_timeout(self):
        if self.captured or self.message is None:
            return
        embed = build_animal_escaped_embed(self.animal)
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(embed=embed, view=self)
        except discord.HTTPException:
            pass


async def spawn_animal(guild: discord.Guild, channel: discord.abc.Messageable) -> None:
    animal = pick_random_animal(guild.id)
    embed = build_animal_spawn_embed(animal)
    view = AnimalCaptureView(animal, guild.id)
    message = await channel.send(embed=embed, view=view)
    view.message = message


@tasks.loop(minutes=ANIMAL_CHECK_INTERVAL_MINUTES)
async def check_animal_spawns():
    now = datetime.now(PARIS_TZ)
    for guild in bot.guilds:
        guild_conf = config.get(str(guild.id), {})
        animal_conf = guild_conf.get("animal_config")
        if not animal_conf or not animal_conf.get("channel_id"):
            continue

        next_spawn_iso = animal_conf.get("next_spawn")
        if not next_spawn_iso:
            # Pas encore de spawn programmé : on en programme un premier
            # (dès aujourd'hui, à une heure aléatoire restante).
            next_dt = compute_next_spawn_datetime(now - timedelta(days=1))
            animal_conf["next_spawn"] = next_dt.isoformat()
            save_config(config)
            continue

        next_dt = datetime.fromisoformat(next_spawn_iso)
        if next_dt.tzinfo is None:
            next_dt = next_dt.replace(tzinfo=PARIS_TZ)

        if now >= next_dt:
            channel = guild.get_channel(animal_conf["channel_id"])
            if channel:
                try:
                    await spawn_animal(guild, channel)
                except discord.HTTPException:
                    pass
            animal_conf["next_spawn"] = compute_next_spawn_datetime(now).isoformat()
            save_config(config)


@check_animal_spawns.before_loop
async def before_check_animal_spawns():
    await bot.wait_until_ready()


# ---- Filet de sécurité pour les quêtes de guilde ----
# Les quêtes sont normalement vérifiées juste après chaque action pertinente
# (message, achat de badge, capture, arrivée d'un membre), mais cette boucle
# passe aussi régulièrement pour rattraper tout cas manqué (redémarrage du
# bot, guilde vérifiée après-coup par le staff, etc.).
@tasks.loop(minutes=2)
async def check_quetes_loop():
    for guild in bot.guilds:
        try:
            await check_and_complete_quetes(guild)
        except Exception as e:
            print(f"⚠️ Erreur lors de la vérification des quêtes sur {guild.name} : {e}")


@check_quetes_loop.before_loop
async def before_check_quetes_loop():
    await bot.wait_until_ready()


animal_group = app_commands.Group(name="animal", description="Système d'animaux à capturer")


@animal_group.command(name="config", description="[Staff] Active/configure le système d'animaux à capturer")
@app_commands.describe(salon="Salon où les animaux sauvages apparaîtront")
async def animal_config_cmd(interaction: discord.Interaction, salon: discord.TextChannel):
    if not is_staff(interaction.user):
        await interaction.response.send_message(
            "❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True
        )
        return

    guild_conf = config.setdefault(str(interaction.guild.id), {})
    animal_conf = guild_conf.setdefault("animal_config", {})
    animal_conf["channel_id"] = salon.id
    if "next_spawn" not in animal_conf:
        premiere_prog = compute_next_spawn_datetime(datetime.now(PARIS_TZ) - timedelta(days=1))
        animal_conf["next_spawn"] = premiere_prog.isoformat()
    save_config(config)

    await interaction.response.send_message(
        f"✅ Système d'animaux activé ! Un animal apparaîtra chaque jour à une heure aléatoire "
        f"(entre {ANIMAL_SPAWN_HOUR_MIN}h et {ANIMAL_SPAWN_HOUR_MAX}h) dans {salon.mention}.",
        ephemeral=True,
    )


@animal_group.command(name="desactiver", description="[Staff] Désactive le système d'animaux")
async def animal_desactiver_cmd(interaction: discord.Interaction):
    if not is_staff(interaction.user):
        await interaction.response.send_message(
            "❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True
        )
        return

    guild_conf = config.setdefault(str(interaction.guild.id), {})
    if "animal_config" in guild_conf:
        del guild_conf["animal_config"]
        save_config(config)

    await interaction.response.send_message("✅ Système d'animaux désactivé sur ce serveur.", ephemeral=True)


@animal_group.command(name="forcespawn", description="[Staff] Force l'apparition immédiate d'un animal")
async def animal_forcespawn_cmd(interaction: discord.Interaction):
    if not is_staff(interaction.user):
        await interaction.response.send_message(
            "❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True
        )
        return

    guild_conf = config.get(str(interaction.guild.id), {})
    animal_conf = guild_conf.get("animal_config")
    if not animal_conf or not animal_conf.get("channel_id"):
        await interaction.response.send_message(
            "❌ Le système d'animaux n'est pas configuré. Utilise `/animal config` d'abord.", ephemeral=True
        )
        return

    channel = interaction.guild.get_channel(animal_conf["channel_id"])
    if channel is None:
        await interaction.response.send_message("❌ Le salon configuré est introuvable.", ephemeral=True)
        return

    await interaction.response.send_message(f"✅ Un animal va apparaître dans {channel.mention} !", ephemeral=True)
    await spawn_animal(interaction.guild, channel)


@animal_group.command(name="collection", description="Affiche les animaux que tu as capturés")
async def animal_collection_cmd(interaction: discord.Interaction):
    guild_conf = config.get(str(interaction.guild.id), {})
    collections = guild_conf.get("animal_collections", {})
    mes_animaux = collections.get(str(interaction.user.id), [])

    if not mes_animaux:
        await interaction.response.send_message("Tu n'as encore capturé aucun animal.", ephemeral=True)
        return

    compteur: dict[str, int] = {}
    for nom in mes_animaux:
        compteur[nom] = compteur.get(nom, 0) + 1

    lignes = []
    for nom, count in sorted(compteur.items(), key=lambda x: -x[1]):
        rarete = next((a["rarete"] for a in ANIMAUX if a["nom"] == nom), "?")
        lignes.append(f"**{nom}** ({rarete}) x{count}")

    embed = discord.Embed(
        title=f"🐾 Collection de {interaction.user.display_name}",
        description="\n".join(lignes),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text=f"{len(mes_animaux)} capture(s) au total")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@animal_group.command(name="classement", description="Affiche le classement des meilleurs chasseurs d'animaux")
async def animal_classement_cmd(interaction: discord.Interaction):
    guild_conf = config.get(str(interaction.guild.id), {})
    collections = guild_conf.get("animal_collections", {})

    if not collections:
        await interaction.response.send_message("Personne n'a encore capturé d'animal sur ce serveur.", ephemeral=True)
        return

    classement = sorted(collections.items(), key=lambda x: -len(x[1]))[:10]
    lignes = []
    for i, (uid, animaux) in enumerate(classement, start=1):
        member = interaction.guild.get_member(int(uid))
        nom = member.mention if member else f"<@{uid}>"
        lignes.append(f"**#{i}** — {nom} : {len(animaux)} capture(s)")

    embed = discord.Embed(
        title="🏆 Classement des chasseurs d'animaux",
        description="\n".join(lignes),
        color=discord.Color.gold(),
    )
    await interaction.response.send_message(embed=embed)


bot.tree.add_command(animal_group)
 
 
# ================================================================
#      INVENTAIRE ET ÉCHANGES D'ANIMAUX (/pet inventory, /pet trade)
# ================================================================
#
# /pet trade propose un échange 1 contre 1 : l'initiateur choisit un de ses
# animaux à donner et un animal que la cible possède déjà à recevoir. La
# cible doit ensuite cliquer sur "Accepter" pour que l'échange soit effectué.

def get_animal_counts(guild_id: int, user_id: int) -> dict:
    """Retourne {nom_animal: quantité} pour un membre donné."""
    guild_conf = config.get(str(guild_id), {})
    animaux_liste = guild_conf.get("animal_collections", {}).get(str(user_id), [])
    compteur: dict[str, int] = {}
    for nom in animaux_liste:
        compteur[nom] = compteur.get(nom, 0) + 1
    return compteur


def get_animal_rarete(nom: str) -> str:
    return next((a["rarete"] for a in ANIMAUX if a["nom"] == nom), "?")


class PetTradeConfirmView(discord.ui.View):
    """Vue affichée dans le salon, visible par la cible de l'échange, qui doit
    accepter ou refuser."""

    def __init__(self, initiateur: discord.Member, cible: discord.Member, animal_initiateur: str, animal_cible: str):
        super().__init__(timeout=300)
        self.initiateur = initiateur
        self.cible = cible
        self.animal_initiateur = animal_initiateur
        self.animal_cible = animal_cible
        self.resolved = False
        self.message: discord.Message | None = None

    @discord.ui.button(label="✅ Accepter", style=discord.ButtonStyle.success)
    async def accepter(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.cible.id:
            await interaction.response.send_message(
                "❌ Seul(e) la personne visée par cet échange peut y répondre.", ephemeral=True
            )
            return
        if self.resolved:
            return
        self.resolved = True

        guild_conf = config.setdefault(str(interaction.guild.id), {})
        collections = guild_conf.setdefault("animal_collections", {})
        animaux_initiateur = collections.setdefault(str(self.initiateur.id), [])
        animaux_cible = collections.setdefault(str(self.cible.id), [])

        if self.animal_initiateur not in animaux_initiateur or self.animal_cible not in animaux_cible:
            self.stop()
            for child in self.children:
                child.disabled = True
            await interaction.response.edit_message(
                content="❌ L'un des deux animaux n'est plus disponible (déjà échangé ?). Échange annulé.",
                embed=None,
                view=self,
            )
            return

        animaux_initiateur.remove(self.animal_initiateur)
        animaux_initiateur.append(self.animal_cible)
        animaux_cible.remove(self.animal_cible)
        animaux_cible.append(self.animal_initiateur)
        save_config(config)

        self.stop()
        for child in self.children:
            child.disabled = True

        embed = discord.Embed(
            title="✅ Échange effectué !",
            description=(
                f"{self.initiateur.mention} a donné **{self.animal_initiateur}** et reçu **{self.animal_cible}**.\n"
                f"{self.cible.mention} a donné **{self.animal_cible}** et reçu **{self.animal_initiateur}**."
            ),
            color=discord.Color.green(),
        )
        await interaction.response.edit_message(content=None, embed=embed, view=self)

    @discord.ui.button(label="❌ Refuser", style=discord.ButtonStyle.danger)
    async def refuser(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.cible.id:
            await interaction.response.send_message(
                "❌ Seul(e) la personne visée par cet échange peut y répondre.", ephemeral=True
            )
            return
        if self.resolved:
            return
        self.resolved = True
        self.stop()
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=f"❌ {self.cible.mention} a refusé l'échange.", embed=None, view=self
        )

    async def on_timeout(self):
        if self.resolved or self.message is None:
            return
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(content="⏱️ Cet échange a expiré (personne n'a répondu à temps).", embed=None, view=self)
        except discord.HTTPException:
            pass


class PetTradeSetupView(discord.ui.View):
    """Vue éphémère (visible seulement par l'initiateur) pour choisir les deux
    animaux concernés avant d'envoyer la proposition."""

    def __init__(self, initiateur: discord.Member, cible: discord.Member, mes_animaux: list, leurs_animaux: list):
        super().__init__(timeout=120)
        self.initiateur = initiateur
        self.cible = cible
        self.mon_choix: str | None = None
        self.leur_choix: str | None = None

        self.select_mon_animal = discord.ui.Select(
            placeholder="Ton animal à proposer",
            options=self._build_options(mes_animaux),
            min_values=1,
            max_values=1,
        )
        self.select_mon_animal.callback = self.on_select_mon_animal
        self.add_item(self.select_mon_animal)

        self.select_leur_animal = discord.ui.Select(
            placeholder=f"Animal de {cible.display_name} à recevoir",
            options=self._build_options(leurs_animaux),
            min_values=1,
            max_values=1,
        )
        self.select_leur_animal.callback = self.on_select_leur_animal
        self.add_item(self.select_leur_animal)

        self.bouton_proposer = discord.ui.Button(
            label="Proposer l'échange", style=discord.ButtonStyle.primary, disabled=True
        )
        self.bouton_proposer.callback = self.on_proposer
        self.add_item(self.bouton_proposer)

    @staticmethod
    def _build_options(animaux_liste: list) -> list:
        uniques = sorted(set(animaux_liste))
        options = []
        for nom in uniques[:25]:
            rarete = get_animal_rarete(nom)
            count = animaux_liste.count(nom)
            options.append(discord.SelectOption(label=nom, description=f"{rarete} • x{count}", value=nom))
        return options

    def _maj_bouton(self):
        self.bouton_proposer.disabled = not (self.mon_choix and self.leur_choix)

    async def on_select_mon_animal(self, interaction: discord.Interaction):
        if interaction.user.id != self.initiateur.id:
            await interaction.response.send_message("❌ Ce n'est pas ton échange.", ephemeral=True)
            return
        self.mon_choix = self.select_mon_animal.values[0]
        self._maj_bouton()
        await interaction.response.edit_message(view=self)

    async def on_select_leur_animal(self, interaction: discord.Interaction):
        if interaction.user.id != self.initiateur.id:
            await interaction.response.send_message("❌ Ce n'est pas ton échange.", ephemeral=True)
            return
        self.leur_choix = self.select_leur_animal.values[0]
        self._maj_bouton()
        await interaction.response.edit_message(view=self)

    async def on_proposer(self, interaction: discord.Interaction):
        if interaction.user.id != self.initiateur.id:
            await interaction.response.send_message("❌ Ce n'est pas ton échange.", ephemeral=True)
            return

        guild_conf = config.get(str(interaction.guild.id), {})
        collections = guild_conf.get("animal_collections", {})
        mes_animaux_actuels = collections.get(str(self.initiateur.id), [])
        leurs_animaux_actuels = collections.get(str(self.cible.id), [])

        if self.mon_choix not in mes_animaux_actuels:
            self.stop()
            await interaction.response.edit_message(
                content=f"❌ Tu ne possèdes plus **{self.mon_choix}**. Échange annulé.", view=None
            )
            return
        if self.leur_choix not in leurs_animaux_actuels:
            self.stop()
            await interaction.response.edit_message(
                content=f"❌ {self.cible.mention} ne possède plus **{self.leur_choix}**. Échange annulé.", view=None
            )
            return

        self.stop()
        await interaction.response.edit_message(content=f"✅ Proposition envoyée à {self.cible.mention} !", view=None)

        rarete_mon = get_animal_rarete(self.mon_choix)
        rarete_leur = get_animal_rarete(self.leur_choix)
        embed = discord.Embed(
            title="🔄 Proposition d'échange",
            description=(
                f"{self.initiateur.mention} propose un échange à {self.cible.mention} :\n\n"
                f"**{self.initiateur.display_name}** donne : **{self.mon_choix}** ({rarete_mon})\n"
                f"**{self.cible.display_name}** donne : **{self.leur_choix}** ({rarete_leur})"
            ),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"{self.cible.display_name}, clique ci-dessous pour répondre (5 min).")

        confirm_view = PetTradeConfirmView(self.initiateur, self.cible, self.mon_choix, self.leur_choix)
        message = await interaction.channel.send(content=self.cible.mention, embed=embed, view=confirm_view)
        confirm_view.message = message

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


pet_group = app_commands.Group(name="pet", description="Gère tes animaux capturés")


@pet_group.command(name="inventory", description="Affiche les animaux que tu as capturés")
async def pet_inventory_cmd(interaction: discord.Interaction):
    compteur = get_animal_counts(interaction.guild.id, interaction.user.id)

    if not compteur:
        await interaction.response.send_message("Tu n'as encore capturé aucun animal.", ephemeral=True)
        return

    lignes = []
    for nom, count in sorted(compteur.items(), key=lambda x: -x[1]):
        rarete = get_animal_rarete(nom)
        lignes.append(f"**{nom}** ({rarete}) x{count}")

    embed = discord.Embed(
        title=f"🐾 Inventaire de {interaction.user.display_name}",
        description="\n".join(lignes),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text=f"{sum(compteur.values())} capture(s) au total")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@pet_group.command(name="trade", description="Propose un échange d'animal avec un autre membre")
@app_commands.describe(membre="Le membre avec qui échanger un animal")
async def pet_trade_cmd(interaction: discord.Interaction, membre: discord.Member):
    if membre.id == interaction.user.id:
        await interaction.response.send_message("❌ Tu ne peux pas échanger avec toi-même.", ephemeral=True)
        return
    if membre.bot:
        await interaction.response.send_message("❌ Tu ne peux pas échanger avec un bot.", ephemeral=True)
        return

    guild_conf = config.get(str(interaction.guild.id), {})
    collections = guild_conf.get("animal_collections", {})
    mes_animaux = collections.get(str(interaction.user.id), [])
    leurs_animaux = collections.get(str(membre.id), [])

    if not mes_animaux:
        await interaction.response.send_message("❌ Tu n'as aucun animal à échanger.", ephemeral=True)
        return
    if not leurs_animaux:
        await interaction.response.send_message(f"❌ {membre.mention} n'a aucun animal à échanger.", ephemeral=True)
        return

    view = PetTradeSetupView(interaction.user, membre, mes_animaux, leurs_animaux)
    await interaction.response.send_message(
        f"🔄 Configure ton échange avec {membre.mention} : choisis l'animal que tu proposes et celui que tu veux recevoir.",
        view=view,
        ephemeral=True,
    )


@pet_group.command(name="spawn", description="[Staff] Force l'apparition immédiate d'un animal sauvage")
async def pet_spawn_cmd(interaction: discord.Interaction):
    if not is_staff(interaction.user):
        await interaction.response.send_message(
            "❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True
        )
        return

    guild_conf = config.get(str(interaction.guild.id), {})
    animal_conf = guild_conf.get("animal_config")
    if not animal_conf or not animal_conf.get("channel_id"):
        await interaction.response.send_message(
            "❌ Le système d'animaux n'est pas configuré. Utilise `/animal config` d'abord.", ephemeral=True
        )
        return

    channel = interaction.guild.get_channel(animal_conf["channel_id"])
    if channel is None:
        await interaction.response.send_message("❌ Le salon configuré est introuvable.", ephemeral=True)
        return

    await interaction.response.send_message(f"✅ Un animal va apparaître dans {channel.mention} !", ephemeral=True)
    await spawn_animal(interaction.guild, channel)


bot.tree.add_command(pet_group)
 
 
# ================================================================
#           PANNEAU D'ADMINISTRATION (/admin panel)
# ================================================================
#
# Panneau (visible uniquement par la personne qui l'ouvre) avec des boutons
# pour déclencher des événements liés au système d'animaux : un boost de
# chance temporaire et l'apparition de plusieurs animaux d'un coup.

LUCK_BOOST_MULTIPLIER = 10
LUCK_BOOST_DURATION_SECONDS = 90  # 1 min 30
MASS_SPAWN_COUNT = 10
MASS_SPAWN_DELAY_SECONDS = 1.5  # petite pause entre chaque spawn pour ne pas saturer le salon


class AdminPanelView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=180)
        self.guild_id = guild_id

    @discord.ui.button(label="🍀 Luck x10 (1 min 30)", style=discord.ButtonStyle.success)
    async def luck_boost(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            await interaction.response.send_message(
                "❌ Tu n'as pas la permission d'utiliser ce panneau.", ephemeral=True
            )
            return

        LUCK_BOOST[self.guild_id] = {
            "multiplier": LUCK_BOOST_MULTIPLIER,
            "expires_at": datetime.now(PARIS_TZ) + timedelta(seconds=LUCK_BOOST_DURATION_SECONDS),
        }

        await interaction.response.send_message(
            f"🍀 Chance x{LUCK_BOOST_MULTIPLIER} activée pendant {LUCK_BOOST_DURATION_SECONDS // 60} min "
            f"{LUCK_BOOST_DURATION_SECONDS % 60} s !",
            ephemeral=True,
        )

        guild_conf = config.get(str(self.guild_id), {})
        animal_conf = guild_conf.get("animal_config")
        if animal_conf and animal_conf.get("channel_id"):
            channel = interaction.guild.get_channel(animal_conf["channel_id"])
            if channel:
                try:
                    await channel.send(
                        "🍀✨ **ÉVÉNEMENT CHANCE x10 !** Pendant 1 min 30, les animaux rares ont bien plus "
                        "de chances d'apparaître. Restez à l'affût 👀"
                    )
                except discord.HTTPException:
                    pass

    @discord.ui.button(label="✨ Spawn x10", style=discord.ButtonStyle.primary)
    async def spawn_ten(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            await interaction.response.send_message(
                "❌ Tu n'as pas la permission d'utiliser ce panneau.", ephemeral=True
            )
            return

        guild_conf = config.get(str(self.guild_id), {})
        animal_conf = guild_conf.get("animal_config")
        if not animal_conf or not animal_conf.get("channel_id"):
            await interaction.response.send_message(
                "❌ Le système d'animaux n'est pas configuré. Utilise `/animal config` d'abord.", ephemeral=True
            )
            return

        channel = interaction.guild.get_channel(animal_conf["channel_id"])
        if channel is None:
            await interaction.response.send_message("❌ Le salon configuré est introuvable.", ephemeral=True)
            return

        await interaction.response.send_message(
            f"✨ {MASS_SPAWN_COUNT} animaux vont apparaître dans {channel.mention} !", ephemeral=True
        )
        for _ in range(MASS_SPAWN_COUNT):
            try:
                await spawn_animal(interaction.guild, channel)
            except discord.HTTPException:
                pass
            await asyncio.sleep(MASS_SPAWN_DELAY_SECONDS)


admin_group = app_commands.Group(name="admin", description="Commandes d'administration du bot")


@admin_group.command(name="panel", description="[Staff] Ouvre le panneau d'administration (boost de chance, spawns multiples...)")
async def admin_panel_cmd(interaction: discord.Interaction):
    if not is_staff(interaction.user):
        await interaction.response.send_message(
            "❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True
        )
        return

    embed = discord.Embed(
        title="🛠️ Panneau d'administration",
        description=(
            "**🍀 Luck x10 (1 min 30)** — multiplie par 10 les chances des raretés autres que "
            "Membre pendant 1 min 30.\n"
            "**✨ Spawn x10** — fait apparaître 10 animaux d'un coup dans le salon configuré."
        ),
        color=discord.Color.dark_gold(),
    )
    view = AdminPanelView(interaction.guild.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


bot.tree.add_command(admin_group)
 
 
# ================================================================
#           MODE MAINTENANCE (/maintenance serveur)
# ================================================================
#
# Réservé au PROPRIÉTAIRE du serveur (guild.owner_id), pas seulement aux
# administrateurs. Bascule (toggle) : la première utilisation verrouille
# tous les salons pour @everyone (seul le staff garde l'accès), la
# deuxième utilisation restaure l'état précédent.

MAINTENANCE_ACTION_DELAY_SECONDS = 0.5  # petite pause entre chaque salon pour éviter le rate-limit Discord


def is_server_owner(interaction: discord.Interaction) -> bool:
    return interaction.guild is not None and interaction.guild.owner_id == interaction.user.id


async def activer_maintenance(guild: discord.Guild) -> dict:
    """Rend tous les salons invisibles pour @everyone (le staff garde l'accès).
    Retourne un dict {channel_id: ancienne_valeur_view_channel} pour pouvoir restaurer plus tard."""
    staff_role = discord.utils.get(guild.roles, name=STAFF_ROLE_NAME)
    sauvegarde = {}

    for channel in guild.channels:
        try:
            overwrite_everyone = channel.overwrites_for(guild.default_role)
            sauvegarde[str(channel.id)] = overwrite_everyone.view_channel  # True / False / None

            overwrite_everyone.view_channel = False
            await channel.set_permissions(guild.default_role, overwrite=overwrite_everyone, reason="Mode maintenance activé")

            if staff_role:
                overwrite_staff = channel.overwrites_for(staff_role)
                overwrite_staff.view_channel = True
                await channel.set_permissions(staff_role, overwrite=overwrite_staff, reason="Mode maintenance activé (accès staff)")
        except (discord.Forbidden, discord.HTTPException):
            pass
        await asyncio.sleep(MAINTENANCE_ACTION_DELAY_SECONDS)

    return sauvegarde


async def desactiver_maintenance(guild: discord.Guild, sauvegarde: dict) -> None:
    """Restaure la visibilité des salons telle qu'elle était avant l'activation."""
    for channel in guild.channels:
        try:
            valeur_precedente = sauvegarde.get(str(channel.id), "ABSENTE")
            overwrite_everyone = channel.overwrites_for(guild.default_role)
            overwrite_everyone.view_channel = None if valeur_precedente == "ABSENTE" else valeur_precedente
            await channel.set_permissions(guild.default_role, overwrite=overwrite_everyone, reason="Fin du mode maintenance")
        except (discord.Forbidden, discord.HTTPException):
            pass
        await asyncio.sleep(MAINTENANCE_ACTION_DELAY_SECONDS)


maintenance_group = app_commands.Group(name="maintenance", description="Active/désactive le mode maintenance du serveur")


@maintenance_group.command(
    name="serveur",
    description="[Propriétaire uniquement] Active/désactive le mode maintenance (salons privés sauf staff)",
)
async def maintenance_serveur_cmd(interaction: discord.Interaction):
    if not is_server_owner(interaction):
        await interaction.response.send_message(
            "❌ Seul le propriétaire du serveur peut utiliser cette commande.", ephemeral=True
        )
        return

    guild_conf = config.setdefault(str(interaction.guild.id), {})
    maintenance_conf = guild_conf.get("maintenance", {})

    await interaction.response.defer(ephemeral=True)

    if maintenance_conf.get("active"):
        await desactiver_maintenance(interaction.guild, maintenance_conf.get("saved_overwrites", {}))
        guild_conf["maintenance"] = {"active": False}
        save_config(config)
        await interaction.followup.send(
            "✅ Mode maintenance désactivé. Les salons ont retrouvé leur visibilité normale.", ephemeral=True
        )
    else:
        sauvegarde = await activer_maintenance(interaction.guild)
        guild_conf["maintenance"] = {"active": True, "saved_overwrites": sauvegarde}
        save_config(config)
        await interaction.followup.send(
            "🔒 Mode maintenance activé. Tous les salons sont désormais privés pour tout le monde, "
            "sauf pour le staff. Relance `/maintenance serveur` pour désactiver.",
            ephemeral=True,
        )


bot.tree.add_command(maintenance_group)
 
 
# ================================================================
#              SYSTÈME DE SOUTIENS (/soutiens)
# ================================================================
#
# /soutiens (staff) ouvre une fenêtre pour personnaliser un embed, puis (même
# principe que /ticketsetup : Discord n'autorise pas les menus déroulants
# dans une fenêtre) un menu déroulant listant tous les rôles pour choisir
# lequel attribuer automatiquement.
#
# Ensuite, dès qu'un membre met l'un des textes configurés (ex : "/akuma" ou
# ".gg/akuma") dans son STATUT PERSONNALISÉ Discord, le rôle lui est attribué
# automatiquement (et retiré s'il enlève ce texte de son statut).
#
# ⚠️ Ceci nécessite d'activer l'intent privilégié "Presence Intent" dans le
# Discord Developer Portal (onglet Bot de ton application), en plus des
# intents "Server Members" et "Message Content" déjà nécessaires. Sans ça,
# le bot ne recevra jamais les mises à jour de statut.

class SoutiensRoleSelectView(discord.ui.View):
    """Étape finale de /soutiens : menu déroulant natif listant tous les rôles
    du serveur, pour choisir lequel attribuer automatiquement."""

    def __init__(self, embed_data: dict, triggers_list: list):
        super().__init__(timeout=300)
        self.embed_data = embed_data
        self.triggers_list = triggers_list
        self._done = False

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="Rôle à attribuer automatiquement",
        min_values=1,
        max_values=1,
    )
    async def role_select(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        if self._done:
            return
        self._done = True
        role = select.values[0]

        guild_conf = config.setdefault(str(interaction.guild.id), {})
        guild_conf["soutiens_config"] = {
            "role_id": role.id,
            "triggers": self.triggers_list,
            "titre": self.embed_data["titre"],
            "description": self.embed_data["description"],
        }
        save_config(config)

        embed = discord.Embed(
            title=self.embed_data["titre"],
            description=self.embed_data["description"],
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="✅ Comment obtenir le rôle",
            value=(
                "Mets l'un des textes suivants dans ton **statut personnalisé Discord** :\n"
                + "\n".join(f"`{t}`" for t in self.triggers_list)
            ),
            inline=False,
        )
        embed.set_footer(text=f"Rôle attribué automatiquement : {role.name}")

        self.stop()
        await interaction.response.edit_message(content="✅ Système de soutiens configuré et envoyé ci-dessous !", view=None)
        await interaction.channel.send(embed=embed)


class SoutiensSetupModal(discord.ui.Modal, title="Configuration du système de soutiens"):
    titre = discord.ui.TextInput(
        label="Titre de l'embed",
        placeholder="Ex : 🎉 Soutiens le serveur !",
        max_length=256,
    )
    description = discord.ui.TextInput(
        label="Description de l'embed",
        style=discord.TextStyle.paragraph,
        placeholder="Explique aux membres comment obtenir le rôle...",
        max_length=1000,
    )
    triggers = discord.ui.TextInput(
        label="Textes à détecter (1 par ligne)",
        style=discord.TextStyle.paragraph,
        placeholder="/akuma\n.gg/akuma",
        max_length=200,
    )

    async def on_submit(self, interaction: discord.Interaction):
        triggers_list = [t.strip() for t in self.triggers.value.splitlines() if t.strip()]
        if not triggers_list:
            await interaction.response.send_message(
                "❌ Indique au moins un texte à détecter dans le statut.", ephemeral=True
            )
            return

        embed_data = {"titre": self.titre.value, "description": self.description.value}
        await interaction.response.send_message(
            "🔧 Dernière étape : choisis le rôle à attribuer automatiquement aux membres qui mettent "
            + ", ".join(f"`{t}`" for t in triggers_list)
            + " dans leur statut.",
            view=SoutiensRoleSelectView(embed_data, triggers_list),
            ephemeral=True,
        )


@bot.tree.command(name="soutiens", description="[Staff] Configure et affiche le panneau des soutiens du serveur")
async def soutiens_cmd(interaction: discord.Interaction):
    if not is_staff(interaction.user):
        await interaction.response.send_message(
            "❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True
        )
        return
    await interaction.response.send_modal(SoutiensSetupModal())


@bot.event
async def on_presence_update(before: discord.Member, after: discord.Member):
    guild = after.guild
    if guild is None:
        return

    guild_conf = config.get(str(guild.id), {})
    soutiens_conf = guild_conf.get("soutiens_config")
    if not soutiens_conf:
        return

    role = guild.get_role(soutiens_conf.get("role_id"))
    if role is None:
        return

    triggers = [t.lower() for t in soutiens_conf.get("triggers", [])]
    if not triggers:
        return

    statut_texte = ""
    for activity in after.activities:
        if isinstance(activity, discord.CustomActivity) and activity.name:
            statut_texte = activity.name.lower()
            break

    correspond = any(trigger in statut_texte for trigger in triggers)
    a_deja_le_role = role in after.roles

    try:
        if correspond and not a_deja_le_role:
            await after.add_roles(role, reason="Statut de soutien détecté")
            print(f"[soutiens] Rôle '{role.name}' attribué à {after} sur {guild.name}.")
        elif not correspond and a_deja_le_role:
            await after.remove_roles(role, reason="Statut de soutien retiré")
            print(f"[soutiens] Rôle '{role.name}' retiré à {after} sur {guild.name}.")
    except discord.Forbidden:
        print(
            f"[soutiens] ❌ Permission refusée pour attribuer/retirer '{role.name}' à {after} sur {guild.name}. "
            "Vérifie que le rôle du bot est placé AU-DESSUS du rôle de soutien dans Paramètres du serveur → Rôles, "
            "et que le bot a bien la permission 'Gérer les rôles'."
        )
    except discord.HTTPException as e:
        print(f"[soutiens] ⚠️ Erreur HTTP lors de l'attribution du rôle à {after} : {e}")
 
 
# ================================================================
#                    SYSTÈME ANTI-FLOOD
# ================================================================
#
# Contrairement à l'anti-spam (même message répété), l'anti-flood surveille
# le NOMBRE de messages envoyés en peu de temps, peu importe leur contenu.
# Au-delà du seuil, le membre est automatiquement mute (timeout) quelques
# instants. Le staff n'est pas concerné par cette limite.

FLOOD_WINDOW_SECONDS = 5      # fenêtre de temps surveillée
FLOOD_THRESHOLD = 5           # nombre de messages autorisés dans cette fenêtre
FLOOD_TIMEOUT_SECONDS = 60    # durée du mute appliqué en cas de flood détecté

flood_tracker: dict[tuple[int, int], list] = {}  # (guild_id, user_id) -> liste d'horodatages récents


async def check_flood(message: discord.Message) -> None:
    if is_staff(message.author):
        return

    cle = (message.guild.id, message.author.id)
    maintenant = datetime.now(PARIS_TZ)
    horodatages = flood_tracker.setdefault(cle, [])
    horodatages.append(maintenant)

    seuil_temps = maintenant - timedelta(seconds=FLOOD_WINDOW_SECONDS)
    horodatages[:] = [t for t in horodatages if t >= seuil_temps]

    if len(horodatages) >= FLOOD_THRESHOLD:
        horodatages.clear()  # évite de re-déclencher immédiatement après le mute
        try:
            until = discord.utils.utcnow() + timedelta(seconds=FLOOD_TIMEOUT_SECONDS)
            await message.author.timeout(until, reason="Anti-flood : trop de messages envoyés trop rapidement")
            await message.channel.send(
                f"🚫 {message.author.mention} a été mute {FLOOD_TIMEOUT_SECONDS}s pour flood "
                "(trop de messages envoyés trop rapidement)."
            )
        except discord.Forbidden:
            try:
                await message.channel.send(
                    f"⚠️ {message.author.mention}, ralentis un peu — tu envoies des messages trop vite ! "
                    "(je n'ai pas pu te mute automatiquement, vérifie mes permissions)"
                )
            except discord.HTTPException:
                pass
        except discord.HTTPException:
            pass
 
 
 
# ================================================================
#                      +stats serveur
# ================================================================
 
async def get_or_create_stats_channels(guild: discord.Guild):
    category = discord.utils.get(guild.categories, name=STATS_CATEGORY_NAME)
    if category is None:
        category = await guild.create_category(STATS_CATEGORY_NAME)
 
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(connect=False, view_channel=True)
    }
 
    members_channel = discord.utils.find(lambda c: c.name.startswith("⌛️・Membre"), category.voice_channels)
    if members_channel is None:
        members_channel = await guild.create_voice_channel(
            f"⌛️・Membre : {guild.member_count}", category=category, overwrites=overwrites
        )
 
    bots_channel = discord.utils.find(lambda c: c.name.startswith("🩸・Bot"), category.voice_channels)
    if bots_channel is None:
        nb_bots = sum(1 for m in guild.members if m.bot)
        bots_channel = await guild.create_voice_channel(
            f"🩸・Bot : {nb_bots}", category=category, overwrites=overwrites
        )
 
    return members_channel, bots_channel
 
 
@bot.command(name="stats")
async def stats_command(ctx: commands.Context, sous_commande: str = None):
    if sous_commande is None or sous_commande.lower() != "serveur":
        await ctx.send("Utilise `+stats serveur` pour créer/actualiser les statistiques du serveur.")
        return
 
    if not is_staff(ctx.author):
        await ctx.send("❌ Cette commande est réservée au staff.")
        return
 
    await get_or_create_stats_channels(ctx.guild)
    await ctx.send("✅ Les salons de statistiques ont été créés / mis à jour.")
 
    if not update_stats_loop.is_running():
        update_stats_loop.start()
 
 
@tasks.loop(minutes=STATS_UPDATE_INTERVAL_MINUTES)
async def update_stats_loop():
    for guild in bot.guilds:
        category = discord.utils.get(guild.categories, name=STATS_CATEGORY_NAME)
        if category is None:
            continue
 
        nb_members = guild.member_count
        nb_bots = sum(1 for m in guild.members if m.bot)
 
        members_channel = discord.utils.find(lambda c: c.name.startswith("⌛️・Membre"), category.voice_channels)
        bots_channel = discord.utils.find(lambda c: c.name.startswith("🩸・Bot"), category.voice_channels)
 
        try:
            if members_channel and not members_channel.name.endswith(f": {nb_members}"):
                await members_channel.edit(name=f"⌛️・Membre : {nb_members}")
            if bots_channel and not bots_channel.name.endswith(f": {nb_bots}"):
                await bots_channel.edit(name=f"🩸・Bot : {nb_bots}")
        except discord.HTTPException:
            pass
 
 
# ================================================================
#                  /setrole absences [rôle]
# ================================================================
 
@bot.tree.command(name="setrole", description="Autorise un rôle à valider une catégorie (ex: absences)")
@app_commands.describe(categorie="Catégorie concernée", role="Rôle autorisé à valider")
@app_commands.choices(categorie=[app_commands.Choice(name="absences", value="absences")])
async def setrole(interaction: discord.Interaction, categorie: app_commands.Choice[str], role: discord.Role):
    if not is_staff(interaction.user):
        await interaction.response.send_message(
            "❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True
        )
        return
 
    add_validator_role(interaction.guild.id, categorie.value, role.id)
    await interaction.response.send_message(
        f"✅ Le rôle {role.mention} peut désormais valider les **{categorie.value}**.", ephemeral=True
    )
 
# ================================================================
#                  /set updatelogs [salon]
# ================================================================
#
# Envoie (et mémorise) le salon où sont publiées les nouveautés du bot.
# Pour ajouter une nouvelle entrée au changelog, il suffit de compléter
# la liste UPDATE_LOGS ci-dessous.

UPDATE_LOGS = [
    {
        "titre": "🎫 Système de tickets",
        "description": (
            "Ajout de `/ticketsetup` : crée un panneau de tickets entièrement personnalisable "
            "(titre, texte, boutons de couleurs, rôle(s) à ping) directement depuis une fenêtre Discord.\n"
            "Chaque bouton ouvre un salon privé pour l'utilisateur, avec un bouton pour fermer le ticket."
        ),
    },
    {
        "titre": "👑 Élu de la semaine",
        "description": (
            "Chaque dimanche à 00h30 (heure de Paris), le membre ayant envoyé le plus de messages "
            "dans la semaine reçoit automatiquement le rôle **👑 Élu de la semaine** pendant 7 jours.\n"
            "Commandes : `/eludelasemaine` (affiche les règles) et `/forcerelu` (force la sélection, staff)."
        ),
    },
    {
        "titre": "🎂 Système d'anniversaires",
        "description": (
            "Système optionnel : `/anniv config` (staff) active les annonces d'anniversaire dans un salon.\n"
            "Les membres enregistrent leur date avec `/anniversaire create` (jour/mois uniquement, sans année)."
        ),
    },
    {
        "titre": "📱 Notifications TikTok",
        "description": (
            "`/config tiktok` (staff) permet de recevoir une notification à chaque nouvelle vidéo "
            "du compte TikTok suivi."
        ),
    },
    {
        "titre": "🐾 Animaux à capturer",
        "description": (
            "Système optionnel : `/animal config` (staff) fait apparaître un animal sauvage par jour, "
            "à une heure aléatoire. Premier arrivé, premier servi ! Voir sa collection avec `/animal collection`."
        ),
    },
    {
        "titre": "🏰 Classement des guildes & rôle Chef de guilde",
        "description": (
            "`/guilde classement` affiche le classement des guildes les plus actives "
            "(un administrateur peut le réinitialiser avec `/guilde classement reset:True`, "
            "sans jamais affecter les niveaux/XP des guildes).\n"
            "Le fondateur d'une guilde reçoit désormais automatiquement le rôle **Chef de guilde**."
        ),
    },
    {
        "titre": "🛒 Boutique de guilde",
        "description": (
            "Chaque niveau passé rapporte désormais **20$** à la trésorerie de la guilde.\n"
            "`/guilde boutique` affiche les badges disponibles à l'achat, "
            "`/guilde acheter` (fondateur) permet de les acquérir avec l'argent de la guilde."
        ),
    },
    {
        "titre": "🎌 Quêtes de guilde",
        "description": (
            "2 quêtes inspirées d'animés (One Piece, Naruto, Attack on Titan, Demon Slayer...) "
            "sont actives en permanence pour toutes les guildes du serveur.\n"
            "La première guilde qui remplit l'objectif remporte la récompense — les autres ne "
            "peuvent alors plus la valider, et une nouvelle quête la remplace aussitôt.\n"
            "`/guilde quetes` affiche les quêtes en cours et l'historique des dernières complétées."
        ),
    },
    {
        "titre": "⚖️ Équilibrage & nouveaux objets de guilde",
        "description": (
            "L'XP par message est désormais **réduite selon la taille de la guilde** : à 4 membres, "
            "la guilde progresse comme si elle n'en avait que 2. Les petites guildes restent compétitives.\n"
            "**`+block guild <ID>`** — bloque l'XP d'une guilde adverse pendant 30 min (1200$, 1x/jour).\n"
            "**`+xp fast`** — multiplie par 3 l'XP de ta propre guilde pendant 5 min (1x/jour).\n"
            "Ces deux objets sont réservés au fondateur de la guilde."
        ),
    },
    {
        "titre": "👋 Message de bienvenue",
        "description": (
            "`/bienvenue config` (staff) permet de choisir le salon et de personnaliser entièrement "
            "le message envoyé à chaque nouvelle arrivée.\n"
            "Variables disponibles : `{membre}`, `{pseudo}`, `{serveur}`, `{nombre}` — au choix en "
            "encadré coloré ou en message texte simple.\n"
            "`/bienvenue test` permet d'en voir le rendu avant l'arrivée d'un vrai membre."
        ),
    },
    {
        "titre": "✅ Retrait d'avertissements",
        "description": (
            "Nouvelle commande **`+unwarn @membre`** (staff) pour retirer le dernier avertissement "
            "d'un membre.\n"
            "`+unwarn @membre 2` retire un avertissement précis (numéros visibles avec `+warn list`), "
            "et `+unwarn @membre all` les efface tous.\n"
            "Le membre est prévenu en message privé du retrait."
        ),
    },
]


def build_updatelogs_embed() -> discord.Embed:
    embed = discord.Embed(
        title="📢 Nouveautés du bot",
        description="Voici les dernières fonctionnalités ajoutées au bot :",
        color=discord.Color.blurple(),
        timestamp=datetime.utcnow(),
    )
    for item in UPDATE_LOGS:
        embed.add_field(name=item["titre"], value=item["description"], inline=False)
    embed.set_footer(text="Mises à jour du bot")
    return embed


set_group = app_commands.Group(name="set", description="Commandes de configuration du bot")


@set_group.command(name="updatelogs", description="[Staff] Définit le salon des nouveautés du bot et y publie le changelog")
@app_commands.describe(salon="Salon où seront envoyées les nouveautés du bot")
async def set_updatelogs(interaction: discord.Interaction, salon: discord.TextChannel):
    if not is_staff(interaction.user):
        await interaction.response.send_message(
            "❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True
        )
        return

    guild_conf = config.setdefault(str(interaction.guild.id), {})
    guild_conf["updatelogs_channel_id"] = salon.id
    save_config(config)

    embed = build_updatelogs_embed()
    try:
        await salon.send(embed=embed)
    except discord.Forbidden:
        await interaction.response.send_message(
            f"❌ Je n'ai pas la permission d'envoyer de message dans {salon.mention}.", ephemeral=True
        )
        return
    except discord.HTTPException:
        await interaction.response.send_message(
            "❌ Erreur lors de l'envoi des nouveautés dans le salon.", ephemeral=True
        )
        return

    await interaction.response.send_message(
        f"✅ Le salon des nouveautés a été défini sur {salon.mention} et le changelog y a été envoyé.",
        ephemeral=True,
    )


bot.tree.add_command(set_group)


# ================================================================
#        MESSAGE DE BIENVENUE PERSONNALISÉ (/bienvenue)
# ================================================================
#
# Le staff choisit le salon et le texte du message envoyé à chaque nouvelle
# arrivée sur le serveur. Variables utilisables dans le message :
#   {membre}  -> mentionne le nouveau (@Pseudo)
#   {pseudo}  -> son pseudo sans mention
#   {serveur} -> nom du serveur
#   {nombre}  -> nombre total de membres après son arrivée
#
# Deux styles d'affichage : "embed" (encadré coloré, par défaut) ou "texte"
# (message simple, qui permet de vraiment ping le nouveau membre).

BIENVENUE_DEFAULT_MESSAGE = "👋 Bienvenue {membre} sur **{serveur}** ! Tu es notre {nombre}ᵉ membre 🎉"


def format_bienvenue(template: str, member: discord.Member) -> str:
    return (
        template.replace("{membre}", member.mention)
        .replace("{pseudo}", member.display_name)
        .replace("{serveur}", member.guild.name)
        .replace("{nombre}", str(member.guild.member_count))
    )


async def envoyer_message_bienvenue(member: discord.Member) -> None:
    """Envoie le message de bienvenue configuré, s'il y en a un sur ce serveur."""
    conf = config.get(str(member.guild.id), {}).get("bienvenue_config")
    if not conf or not conf.get("channel_id") or not conf.get("actif", True):
        return

    channel = member.guild.get_channel(conf["channel_id"])
    if channel is None:
        return

    texte = format_bienvenue(conf.get("message") or BIENVENUE_DEFAULT_MESSAGE, member)

    try:
        if conf.get("style", "embed") == "texte":
            await channel.send(texte)
        else:
            embed = discord.Embed(
                title=conf.get("titre") or f"👋 Bienvenue sur {member.guild.name} !",
                description=texte,
                color=discord.Color.blurple(),
                timestamp=datetime.now(PARIS_TZ),
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text=f"{member.guild.member_count} membres au total")
            # Mention hors embed : un embed seul ne notifie jamais le membre.
            await channel.send(content=member.mention, embed=embed)
    except discord.HTTPException:
        pass


bienvenue_group = app_commands.Group(name="bienvenue", description="Message de bienvenue des nouveaux membres (staff)")


@bienvenue_group.command(name="config", description="[Staff] Configure le salon et le message de bienvenue")
@app_commands.describe(
    salon="Salon où sera envoyé le message de bienvenue",
    message="Texte du message. Variables : {membre} {pseudo} {serveur} {nombre}",
    style="Affichage : encadré coloré (embed) ou message texte simple",
    titre="Titre de l'encadré (uniquement en style embed)",
)
@app_commands.choices(style=[
    app_commands.Choice(name="Encadré coloré (embed)", value="embed"),
    app_commands.Choice(name="Message texte simple", value="texte"),
])
async def bienvenue_config_cmd(
    interaction: discord.Interaction,
    salon: discord.TextChannel,
    message: str = None,
    style: app_commands.Choice[str] = None,
    titre: str = None,
):
    if not is_staff(interaction.user):
        await interaction.response.send_message(
            "❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True
        )
        return

    guild_conf = config.setdefault(str(interaction.guild.id), {})
    conf = guild_conf.setdefault("bienvenue_config", {})
    conf["channel_id"] = salon.id
    conf["actif"] = True
    if message:
        conf["message"] = message
    if style:
        conf["style"] = style.value
    if titre:
        conf["titre"] = titre
    save_config(config)

    apercu = format_bienvenue(conf.get("message") or BIENVENUE_DEFAULT_MESSAGE, interaction.user)
    await interaction.response.send_message(
        f"✅ Message de bienvenue activé dans {salon.mention}.\n\n"
        f"**Aperçu :**\n{apercu}\n\n"
        "Variables disponibles : `{membre}` `{pseudo}` `{serveur}` `{nombre}`\n"
        "Teste le rendu réel avec `/bienvenue test`.",
        ephemeral=True,
    )


@bienvenue_group.command(name="test", description="[Staff] Envoie un message de bienvenue de test avec toi-même")
async def bienvenue_test_cmd(interaction: discord.Interaction):
    if not is_staff(interaction.user):
        await interaction.response.send_message(
            "❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True
        )
        return

    conf = config.get(str(interaction.guild.id), {}).get("bienvenue_config")
    if not conf or not conf.get("channel_id"):
        await interaction.response.send_message(
            "❌ Le message de bienvenue n'est pas configuré. Utilise `/bienvenue config` d'abord.", ephemeral=True
        )
        return

    await envoyer_message_bienvenue(interaction.user)
    salon = interaction.guild.get_channel(conf["channel_id"])
    await interaction.response.send_message(
        f"✅ Message de test envoyé dans {salon.mention if salon else 'le salon configuré'}.", ephemeral=True
    )


@bienvenue_group.command(name="desactiver", description="[Staff] Désactive le message de bienvenue")
async def bienvenue_desactiver_cmd(interaction: discord.Interaction):
    if not is_staff(interaction.user):
        await interaction.response.send_message(
            "❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True
        )
        return

    guild_conf = config.setdefault(str(interaction.guild.id), {})
    conf = guild_conf.get("bienvenue_config")
    if not conf:
        await interaction.response.send_message("❌ Aucun message de bienvenue n'est configuré.", ephemeral=True)
        return

    conf["actif"] = False
    save_config(config)
    await interaction.response.send_message(
        "✅ Message de bienvenue désactivé. Relance `/bienvenue config` pour le réactiver.", ephemeral=True
    )


bot.tree.add_command(bienvenue_group)

class FormModStep3Modal(discord.ui.Modal, title="Candidature Modérateur (3/3)"):
    complement = discord.ui.TextInput(
        label="Quelque chose à rajouter ?",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500,
    )

    def __init__(self, reponses: dict):
        super().__init__()
        self.reponses = reponses

    async def on_submit(self, interaction: discord.Interaction):
        self.reponses["complement"] = self.complement.value or "Rien à ajouter."
        await envoyer_candidature_mod(interaction, self.reponses)


class FormModContinueView(discord.ui.View):
    """Bouton intermédiaire obligatoire entre deux fenêtres : Discord interdit
    d'ouvrir un nouveau modal directement depuis la soumission d'un autre
    modal, il faut d'abord passer par un clic de bouton (interaction de type
    message_component, qui autorise send_modal)."""

    def __init__(self, next_modal_factory, reponses: dict):
        super().__init__(timeout=300)
        self.next_modal_factory = next_modal_factory
        self.reponses = reponses

    @discord.ui.button(label="Continuer ➡️", style=discord.ButtonStyle.primary)
    async def continuer(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.send_modal(self.next_modal_factory(self.reponses))


class FormModStep2Modal(discord.ui.Modal, title="Candidature Modérateur (2/3)"):
    pourquoi = discord.ui.TextInput(
        label="Pourquoi devenir Modérateur ?",
        style=discord.TextStyle.paragraph,
        max_length=1000,
    )
    role_selon_toi = discord.ui.TextInput(
        label='En quoi consiste le rôle "Modérateur" ?',
        style=discord.TextStyle.paragraph,
        max_length=1000,
    )

    def __init__(self, reponses: dict):
        super().__init__()
        self.reponses = reponses

    async def on_submit(self, interaction: discord.Interaction):
        self.reponses["pourquoi"] = self.pourquoi.value
        self.reponses["role_selon_toi"] = self.role_selon_toi.value
        await interaction.response.send_message(
            "✅ Étape 2/3 reçue ! Clique ci-dessous pour la dernière étape.",
            view=FormModContinueView(FormModStep3Modal, self.reponses),
            ephemeral=True,
        )


class FormModStep1Modal(discord.ui.Modal, title="Candidature Modérateur (1/3)"):
    pseudo = discord.ui.TextInput(label="Quel est ton pseudo Discord ?", max_length=100)
    age = discord.ui.TextInput(label="Quel âge as-tu ?", max_length=10)
    disponibilites = discord.ui.TextInput(
        label="Tes disponibilités pour ce travail",
        style=discord.TextStyle.paragraph,
        max_length=500,
    )

    async def on_submit(self, interaction: discord.Interaction):
        reponses = {
            "pseudo": self.pseudo.value,
            "age": self.age.value,
            "disponibilites": self.disponibilites.value,
        }
        await interaction.response.send_message(
            "✅ Étape 1/3 reçue ! Clique ci-dessous pour continuer.",
            view=FormModContinueView(FormModStep2Modal, reponses),
            ephemeral=True,
        )


async def envoyer_candidature_mod(interaction: discord.Interaction, reponses: dict) -> None:
    guild_conf = config.get(str(interaction.guild.id), {})
    channel_id = guild_conf.get("form_mod_channel_id")

    if not channel_id:
        await interaction.response.send_message(
            "✅ Ta candidature a bien été enregistrée, mais aucun salon n'est configuré pour la recevoir. "
            "Préviens le staff (`/form config`).",
            ephemeral=True,
        )
        return

    channel = interaction.guild.get_channel(channel_id)
    if channel is None:
        await interaction.response.send_message(
            "✅ Ta candidature a bien été enregistrée, mais le salon configuré est introuvable. Préviens le staff.",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title="📋 Nouvelle candidature — Modérateur",
        color=discord.Color.blurple(),
        timestamp=datetime.now(PARIS_TZ),
    )
    embed.add_field(name="Pseudo Discord", value=reponses["pseudo"], inline=True)
    embed.add_field(name="Âge", value=reponses["age"], inline=True)
    embed.add_field(name="Disponibilités", value=reponses["disponibilites"], inline=False)
    embed.add_field(name="Pourquoi devenir Modérateur ?", value=reponses["pourquoi"], inline=False)
    embed.add_field(name='En quoi consiste le rôle "Modérateur" ?', value=reponses["role_selon_toi"], inline=False)
    embed.add_field(name="Complément", value=reponses["complement"], inline=False)
    embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
    embed.set_footer(text=f"ID : {interaction.user.id}")

    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        pass

    await interaction.response.send_message("✅ Ta candidature a bien été envoyée. Merci !", ephemeral=True)


form_group = app_commands.Group(name="form", description="Formulaires de candidature")


@form_group.command(name="mod", description="Candidater pour devenir Modérateur")
async def form_mod_cmd(interaction: discord.Interaction):
    await interaction.response.send_modal(FormModStep1Modal())


@form_group.command(name="config", description="[Staff] Définit le salon où sont envoyées les candidatures Modérateur")
@app_commands.describe(salon="Salon où seront envoyées les candidatures")
async def form_config_cmd(interaction: discord.Interaction, salon: discord.TextChannel):
    if not is_staff(interaction.user):
        await interaction.response.send_message(
            "❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True
        )
        return

    guild_conf = config.setdefault(str(interaction.guild.id), {})
    guild_conf["form_mod_channel_id"] = salon.id
    save_config(config)

    await interaction.response.send_message(
        f"✅ Les candidatures Modérateur seront désormais envoyées dans {salon.mention}.",
        ephemeral=True,
    )


bot.tree.add_command(form_group)

# ================================================================
#                       +invite-stats
# ================================================================
 
invites_cache: dict[int, dict[str, int]] = {}  # {guild_id: {invite_code: uses}}
 
 
async def update_invites_cache(guild: discord.Guild) -> None:
    try:
        invites = await guild.invites()
        invites_cache[guild.id] = {invite.code: invite.uses for invite in invites}
    except discord.Forbidden:
        invites_cache[guild.id] = {}
 
 
@bot.event
async def on_member_join(member: discord.Member):
    guild = member.guild

    # ---- Message de bienvenue personnalisé ----
    # Envoyé en premier, indépendamment du suivi des invitations : ainsi il
    # part même si le bot n'a pas la permission de lire les invitations.
    await envoyer_message_bienvenue(member)

     # ---- Ping puis suppression immédiate (/mentionjoin) ----
    mentionjoin_channel_id = config.get(str(guild.id), {}).get("mentionjoin_channel_id")
    if mentionjoin_channel_id:
        mentionjoin_channel = guild.get_channel(mentionjoin_channel_id)
        if mentionjoin_channel:
            try:
                ping_msg = await mentionjoin_channel.send(member.mention)
                await ping_msg.delete()
            except discord.HTTPException:
                pass

    # ---- Suivi des invitations ----
    old_invites = invites_cache.get(guild.id, {})

    try:
        new_invites = await guild.invites()
    except discord.Forbidden:
        return
 
    inviter = None
    for invite in new_invites:
        if invite.uses > old_invites.get(invite.code, 0):
            inviter = invite.inviter
            break
 
    invites_cache[guild.id] = {invite.code: invite.uses for invite in new_invites}
 
    if inviter is not None:
        guild_conf = config.setdefault(str(guild.id), {})
        invite_stats = guild_conf.setdefault("invite_stats", {})
        invite_stats[str(inviter.id)] = invite_stats.get(str(inviter.id), 0) + 1
        save_config(config)
 
 
@bot.event
async def on_guild_join(guild: discord.Guild):
    await update_invites_cache(guild)
 
 
@bot.command(name="invite-stats")
async def invite_stats_command(ctx: commands.Context, membre: discord.Member = None):
    membre = membre or ctx.author
    invite_stats = config.get(str(ctx.guild.id), {}).get("invite_stats", {})
    count = invite_stats.get(str(membre.id), 0)
 
    embed = discord.Embed(
        title="📊 Statistiques d'invitations",
        description=f"{membre.mention} a invité **{count}** membre(s) sur ce serveur.",
        color=discord.Color.gold(),
    )
    embed.set_thumbnail(url=membre.display_avatar.url)
    await ctx.send(embed=embed)
 
 
# ================================================================
#                         +concept
# ================================================================
 
def get_concept_list(guild_id: int) -> list:
    return config.get(str(guild_id), {}).get("concept_list", [])
 
 
def save_concept_list(guild_id: int, members: list) -> None:
    guild_conf = config.setdefault(str(guild_id), {})
    guild_conf["concept_list"] = members
    save_config(config)
 
 
@bot.command(name="concept")
async def concept_command(ctx: commands.Context, sous_commande: str = None, membre: discord.Member = None):
    if sous_commande and sous_commande.lower() == "list":
 
        if membre is not None:
            await ctx.send("❌ Utilise `+concept list reset` pour réinitialiser la liste.")
            return
 
        concept_list = get_concept_list(ctx.guild.id)
 
        if not concept_list:
            embed = discord.Embed(
                title="📋 Liste Concept",
                description="Aucun membre n'est actuellement dans la liste.",
                color=discord.Color.blurple()
            )
            await ctx.send(embed=embed)
            return
 
        mentions = []
        for user_id in concept_list:
            member = ctx.guild.get_member(int(user_id))
            if member:
                mentions.append(f"• {member.mention}")
            else:
                mentions.append(f"• <@{user_id}>")
 
        embed = discord.Embed(
            title="📋 Liste Concept",
            description="\n".join(mentions),
            color=discord.Color.blurple()
        )
        embed.set_footer(text=f"{len(concept_list)} membre(s) dans la liste.")
        await ctx.send(embed=embed)
        return
 
    if sous_commande and sous_commande.lower() == "note":
 
        if not is_staff(ctx.author):
            await ctx.send("❌ Cette commande est réservée au staff.")
            return
 
        if membre is None:
            await ctx.send("❌ Utilise `+concept note @membre`.")
            return
 
        concept_list = get_concept_list(ctx.guild.id)
 
        if str(membre.id) in concept_list:
            await ctx.send(f"⚠️ {membre.mention} est déjà dans la liste Concept.")
            return
 
        concept_list.append(str(membre.id))
        save_concept_list(ctx.guild.id, concept_list)
 
        await ctx.send(f"✅ {membre.mention} a été ajouté à la **liste Concept**.")
        return
 
    await ctx.send(
        "❌ Utilisation :\n"
        "`+concept note @membre` — Ajouter un membre (Staff)\n"
        "`+concept list` — Voir la liste\n"
        "`+concept list reset` — Réinitialiser la liste (Staff)"
    )
 
 
# ================================================================
#                    ÉLU DE LA SEMAINE
# ================================================================
 
def get_weekly_counts(guild_id: int) -> dict:
    return config.get(str(guild_id), {}).get("weekly_counts", {})
 
 
def bump_weekly_count(guild_id: int, user_id: int) -> None:
    guild_conf = config.setdefault(str(guild_id), {})
    weekly = guild_conf.setdefault("weekly_counts", {})
    uid = str(user_id)
    weekly[uid] = weekly.get(uid, 0) + 1
    save_config(config)
 
 
def get_elu_actuel(guild_id: int):
    return config.get(str(guild_id), {}).get("elu_actuel")
 
 
async def get_or_create_elu_role(guild: discord.Guild) -> discord.Role | None:
    role = discord.utils.get(guild.roles, name=ELU_ROLE_NAME)
    if role is None:
        try:
            role = await guild.create_role(
                name=ELU_ROLE_NAME,
                color=discord.Color.gold(),
                reason="Création automatique du rôle Élu de la semaine",
            )
        except discord.HTTPException:
            role = None
    return role
 
 
def build_elu_embed(guild: discord.Guild | None = None) -> discord.Embed:
    """Construit l'embed de présentation, dans le même esprit que la capture d'écran."""
    embed = discord.Embed(
        title="👑 • ÉLU DE LA SEMAINE",
        color=discord.Color.from_rgb(20, 20, 24),
    )
    embed.set_image(url=ELU_GIF_URL)
    embed.add_field(
        name="🏆 LE PRINCIPE",
        value=(
            "Chaque semaine, les messages envoyés dans le chat sont comptabilisés.\n\n"
            "Chaque **dimanche à 00h30**, le membre ayant envoyé le plus de messages "
            "devient l'**Élu de la semaine**.\n\n"
            f"👑 Il reçoit le rôle **{ELU_ROLE_NAME}** pendant 7 jours.\n"
            "🔄 Lors de la prochaine sélection, l'ancien rôle est retiré et attribué "
            "au nouveau gagnant.\n"
            "📅 Sélection : dimanche à 00h30 — heure de Paris\n"
            "💬 Seul le chat de la semaine est pris en compte."
        ),
        inline=False,
    )
 
    if guild is not None:
        current_id = get_elu_actuel(guild.id)
        if current_id:
            member = guild.get_member(int(current_id))
            mention = member.mention if member else f"<@{current_id}>"
            embed.add_field(name="👑 Élu actuel", value=mention, inline=False)
        else:
            embed.add_field(name="👑 Élu actuel", value="Aucun élu pour le moment.", inline=False)
 
    return embed
 
 
@bot.tree.command(name="eludelasemaine", description="Affiche les règles de l'Élu de la semaine")
async def eludelasemaine(interaction: discord.Interaction):
    if not is_staff(interaction.user):
        await interaction.response.send_message(
            "❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True
        )
        return
 
    embed = build_elu_embed(interaction.guild)
    await interaction.response.send_message(embed=embed)
 
 
@bot.tree.command(name="forcerelu", description="[Staff] Force la sélection immédiate de l'Élu de la semaine")
async def forcerelu(interaction: discord.Interaction):
    if not is_staff(interaction.user):
        await interaction.response.send_message(
            "❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True
        )
        return
 
    await interaction.response.defer(ephemeral=True)
    await select_elu_semaine(interaction.guild)
    await interaction.followup.send("✅ La sélection de l'Élu de la semaine a été forcée.", ephemeral=True)
 
 
async def select_elu_semaine(guild: discord.Guild) -> None:
    """Sélectionne le membre ayant le plus parlé cette semaine, échange le rôle, reset les compteurs."""
    guild_conf = config.setdefault(str(guild.id), {})
    weekly = guild_conf.get("weekly_counts", {})
 
    role = await get_or_create_elu_role(guild)
 
    # Retire le rôle à l'ancien élu
    ancien_id = guild_conf.get("elu_actuel")
    if role and ancien_id:
        ancien_membre = guild.get_member(int(ancien_id))
        if ancien_membre:
            try:
                await ancien_membre.remove_roles(role, reason="Fin de règne - Élu de la semaine")
            except discord.HTTPException:
                pass
 
    if not weekly:
        guild_conf["elu_actuel"] = None
        guild_conf["weekly_counts"] = {}
        save_config(config)
        return
 
    gagnant_id = max(weekly, key=weekly.get)
    gagnant_membre = guild.get_member(int(gagnant_id))
 
    if role and gagnant_membre:
        try:
            await gagnant_membre.add_roles(role, reason="Élu de la semaine")
        except discord.HTTPException:
            pass
 
    channel = guild.system_channel or discord.utils.get(guild.text_channels, name="général")
    if channel and gagnant_membre:
        try:
            await channel.send(
                content=f"🎉 Félicitations {gagnant_membre.mention}, tu es l'**Élu de la semaine** !",
                embed=build_elu_embed(guild),
            )
        except discord.HTTPException:
            pass
 
    guild_conf["elu_actuel"] = gagnant_id
    guild_conf["weekly_counts"] = {}
    save_config(config)
 
 
@tasks.loop(minutes=1)
async def check_elu_semaine():
    now = datetime.now(PARIS_TZ)
    # weekday() == 6 -> dimanche
    if now.weekday() == 6 and now.hour == 0 and now.minute == 30:
        for guild in bot.guilds:
            await select_elu_semaine(guild)
 
 
@check_elu_semaine.before_loop
async def before_check_elu_semaine():
    await bot.wait_until_ready()
 
 
@bot.tree.command(name="clear", description="[Staff] Supprime un nombre de messages dans le salon")
@app_commands.describe(nombre="Nombre de messages à supprimer (1 à 100)")
async def clear(interaction: discord.Interaction, nombre: app_commands.Range[int, 1, 100]):
    if not is_mod(interaction.user):
        await interaction.response.send_message(
            "❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True
        )
        return
 
    await interaction.response.defer(ephemeral=True)
    try:
        supprimes = await interaction.channel.purge(limit=nombre)
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ Je n'ai pas la permission de supprimer des messages dans ce salon.", ephemeral=True
        )
        return
    except discord.HTTPException:
        await interaction.followup.send(
            "❌ Erreur lors de la suppression des messages (les messages de plus de 14 jours ne peuvent pas être supprimés en masse).",
            ephemeral=True,
        )
        return
 
    await interaction.followup.send(f"✅ {len(supprimes)} message(s) supprimé(s).", ephemeral=True)
 
@bot.tree.command(name="mentionjoin", description="[Staff] Définit le salon où ping+supprimer chaque nouveau membre")
@app_commands.describe(salon="Salon où le nouveau membre sera ping puis le message supprimé")
async def mentionjoin_cmd(interaction: discord.Interaction, salon: discord.TextChannel):
    if not is_staff(interaction.user):
        await interaction.response.send_message(
            "❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True
        )
        return

    guild_conf = config.setdefault(str(interaction.guild.id), {})
    guild_conf["mentionjoin_channel_id"] = salon.id
    save_config(config)

    await interaction.response.send_message(
        f"✅ Chaque nouveau membre sera désormais ping puis le message supprimé immédiatement dans {salon.mention}.",
        ephemeral=True,
    )

    await interaction.response.send_message(membre.mention)
    await interaction.delete_original_response()
 
# ================================================================
#                       MODÉRATION
# ================================================================
 
def get_warns(guild_id: int, user_id: int) -> list:
    return config.get(str(guild_id), {}).get("warns", {}).get(str(user_id), [])
 
 
def add_warn(guild_id: int, user_id: int, moderator_id: int, raison: str) -> int:
    guild_conf = config.setdefault(str(guild_id), {})
    warns = guild_conf.setdefault("warns", {})
    user_warns = warns.setdefault(str(user_id), [])
    user_warns.append({
        "reason": raison,
        "moderator_id": str(moderator_id),
        "date": datetime.utcnow().isoformat(),
    })
    save_config(config)
    return len(user_warns)
 
 
def remove_warn(guild_id: int, user_id: int, index: int = -1):
    """Retire un avertissement. index = -1 pour le dernier, sinon l'index (0-based).
    Retourne (warn_supprimé, nb_restants) ou (None, nb_restants) si introuvable."""
    guild_conf = config.setdefault(str(guild_id), {})
    warns = guild_conf.setdefault("warns", {})
    user_warns = warns.get(str(user_id), [])

    if not user_warns:
        return None, 0
    if index != -1 and not (0 <= index < len(user_warns)):
        return None, len(user_warns)

    supprime = user_warns.pop(index)
    if not user_warns:
        del warns[str(user_id)]
    save_config(config)
    return supprime, len(user_warns)


def clear_warns(guild_id: int, user_id: int) -> int:
    """Supprime tous les avertissements d'un membre. Retourne le nombre supprimé."""
    guild_conf = config.setdefault(str(guild_id), {})
    warns = guild_conf.setdefault("warns", {})
    nb = len(warns.get(str(user_id), []))
    if str(user_id) in warns:
        del warns[str(user_id)]
        save_config(config)
    return nb


@bot.command(name="warn")
async def warn_command(ctx: commands.Context, cible: str = None, *, reste: str = None):
    if not is_helper(ctx.author):
        await ctx.send("❌ Cette commande est réservée au staff (Helper, Mod ou Gérant).")
        return
 
    if cible is None:
        await ctx.send(
            "❌ Utilisation :\n"
            "`+warn @membre <raison>` — Avertir un membre\n"
            "`+warn list @membre` — Voir ses avertissements"
        )
        return
 
    # ---- +warn list @membre ----
    if cible.lower() == "list":
        if reste is None:
            await ctx.send("❌ Utilise `+warn list @membre`.")
            return
        try:
            membre = await commands.MemberConverter().convert(ctx, reste.strip())
        except commands.MemberNotFound:
            await ctx.send("❌ Membre introuvable.")
            return
 
        warns = get_warns(ctx.guild.id, membre.id)
        if not warns:
            embed = discord.Embed(
                title=f"📋 Avertissements de {membre.display_name}",
                description="Aucun avertissement.",
                color=discord.Color.green(),
            )
        else:
            lignes = []
            for i, w in enumerate(warns, start=1):
                date = w["date"][:10]
                mod = ctx.guild.get_member(int(w["moderator_id"]))
                mod_nom = mod.mention if mod else f"<@{w['moderator_id']}>"
                lignes.append(f"**#{i}** — {w['reason']} *(par {mod_nom}, le {date})*")
            embed = discord.Embed(
                title=f"📋 Avertissements de {membre.display_name}",
                description="\n".join(lignes),
                color=discord.Color.orange(),
            )
        embed.set_footer(text=f"{len(warns)} avertissement(s)")
        embed.set_thumbnail(url=membre.display_avatar.url)
        await ctx.send(embed=embed)
        return
 
    # ---- +warn @membre <raison> ----
    try:
        membre = await commands.MemberConverter().convert(ctx, cible)
    except commands.MemberNotFound:
        await ctx.send("❌ Membre introuvable.")
        return
 
    raison = reste or "Aucune raison précisée."
    total = add_warn(ctx.guild.id, membre.id, ctx.author.id, raison)
 
    embed = discord.Embed(title="⚠️ Avertissement", color=discord.Color.orange())
    embed.add_field(name="Membre", value=membre.mention, inline=True)
    embed.add_field(name="Modérateur", value=ctx.author.mention, inline=True)
    embed.add_field(name="Raison", value=raison, inline=False)
    embed.set_footer(text=f"{membre.display_name} a désormais {total} avertissement(s).")
    await ctx.send(embed=embed)
 
    try:
        await membre.send(
            f"⚠️ Tu as reçu un avertissement sur **{ctx.guild.name}**.\nRaison : {raison}"
        )
    except discord.HTTPException:
        pass
 
 
@bot.command(name="unwarn")
async def unwarn_command(ctx: commands.Context, cible: str = None, numero: str = None):
    """+unwarn @membre          -> retire le dernier avertissement
       +unwarn @membre 2        -> retire l'avertissement n°2
       +unwarn @membre all      -> retire tous les avertissements"""
    if not is_gerant(ctx.author):
        await ctx.send("❌ Cette commande est réservée aux Gérants (les Helpers et Mods ne peuvent pas retirer de warns).")
        return

    if cible is None:
        await ctx.send(
            "❌ Utilisation :\n"
            "`+unwarn @membre` — Retire le dernier avertissement\n"
            "`+unwarn @membre 2` — Retire l'avertissement n°2 (numéros visibles avec `+warn list @membre`)\n"
            "`+unwarn @membre all` — Retire tous les avertissements"
        )
        return

    try:
        membre = await commands.MemberConverter().convert(ctx, cible)
    except commands.MemberNotFound:
        await ctx.send("❌ Membre introuvable.")
        return

    warns = get_warns(ctx.guild.id, membre.id)
    if not warns:
        await ctx.send(f"❌ {membre.mention} n'a aucun avertissement.")
        return

    # ---- +unwarn @membre all ----
    if numero and numero.lower() in {"all", "tout", "tous"}:
        nb = clear_warns(ctx.guild.id, membre.id)
        embed = discord.Embed(
            title="🧹 Avertissements effacés",
            description=f"Les **{nb}** avertissement(s) de {membre.mention} ont été supprimés.",
            color=discord.Color.green(),
        )
        embed.set_footer(text=f"Par {ctx.author}")
        await ctx.send(embed=embed)

        try:
            await membre.send(
                f"✅ Tous tes avertissements sur **{ctx.guild.name}** ont été retirés par le staff."
            )
        except discord.HTTPException:
            pass
        return

    # ---- +unwarn @membre [numéro] ----
    index = -1
    if numero is not None:
        try:
            saisi = int(numero)
        except ValueError:
            await ctx.send("❌ Numéro invalide. Utilise un nombre, ou `all` pour tout retirer.")
            return
        if not (1 <= saisi <= len(warns)):
            await ctx.send(
                f"❌ Numéro invalide : {membre.display_name} a **{len(warns)}** avertissement(s). "
                "Vérifie les numéros avec `+warn list @membre`."
            )
            return
        index = saisi - 1

    supprime, restants = remove_warn(ctx.guild.id, membre.id, index)
    if supprime is None:
        await ctx.send("❌ Impossible de retirer cet avertissement.")
        return

    embed = discord.Embed(title="✅ Avertissement retiré", color=discord.Color.green())
    embed.add_field(name="Membre", value=membre.mention, inline=True)
    embed.add_field(name="Retiré par", value=ctx.author.mention, inline=True)
    embed.add_field(name="Raison de l'avertissement", value=supprime.get("reason", "—"), inline=False)
    embed.set_footer(text=f"{membre.display_name} a désormais {restants} avertissement(s).")
    await ctx.send(embed=embed)

    try:
        await membre.send(
            f"✅ Un de tes avertissements sur **{ctx.guild.name}** a été retiré.\n"
            f"Il t'en reste **{restants}**."
        )
    except discord.HTTPException:
        pass
 
 
def parse_duration(duree_str: str):
    """Convertit '10m', '2h', '1j'/'1d', '30s', '2w' (semaine) en secondes. Retourne None si invalide."""
    unites = {"s": 1, "m": 60, "h": 3600, "j": 86400, "d": 86400, "w": 7 * 86400}
    if not duree_str or len(duree_str) < 2:
        return None
    unite = duree_str[-1].lower()
    if unite not in unites:
        return None
    try:
        valeur = int(duree_str[:-1])
    except ValueError:
        return None
    if valeur <= 0:
        return None
    return valeur * unites[unite]
 
 
@bot.command(name="mute")
async def mute_command(ctx: commands.Context, membre: discord.Member = None, duree: str = None, *, raison: str = None):
    if not is_mod(ctx.author):
        await ctx.send("❌ Cette commande est réservée aux Mods et Gérants.")
        return
 
    if membre is None or duree is None:
        await ctx.send("❌ Utilisation : `+mute @membre <durée ex: 10m/2h/1j> [raison]`")
        return
 
    secondes = parse_duration(duree)
    if secondes is None:
        await ctx.send("❌ Durée invalide. Utilise un format comme `10m`, `2h`, `1j`.")
        return
 
    secondes = min(secondes, 28 * 86400)  # Discord limite le timeout à 28 jours max
    raison = raison or "Aucune raison précisée."
    until = discord.utils.utcnow() + timedelta(seconds=secondes)
 
    try:
        await membre.timeout(until, reason=f"{raison} (par {ctx.author})")
    except discord.Forbidden:
        await ctx.send("❌ Je n'ai pas la permission de mute ce membre (vérifie la position de mon rôle).")
        return
    except discord.HTTPException:
        await ctx.send("❌ Erreur lors du mute.")
        return
 
    embed = discord.Embed(title="🔇 Membre mute", color=discord.Color.dark_grey())
    embed.add_field(name="Membre", value=membre.mention, inline=True)
    embed.add_field(name="Durée", value=duree, inline=True)
    embed.add_field(name="Raison", value=raison, inline=False)
    embed.set_footer(text=f"Par {ctx.author}")
    await ctx.send(embed=embed)
 
    try:
        await membre.send(
            f"🔇 Tu as été rendu muet sur **{ctx.guild.name}** pour {duree}.\nRaison : {raison}"
        )
    except discord.HTTPException:
        pass
 
 
@bot.command(name="unmute")
async def unmute_command(ctx: commands.Context, membre: discord.Member = None):
    if not is_mod(ctx.author):
        await ctx.send("❌ Cette commande est réservée aux Mods et Gérants.")
        return
 
    if membre is None:
        await ctx.send("❌ Utilisation : `+unmute @membre`")
        return
 
    try:
        await membre.timeout(None, reason=f"Unmute par {ctx.author}")
    except discord.Forbidden:
        await ctx.send("❌ Je n'ai pas la permission de unmute ce membre.")
        return
    except discord.HTTPException:
        await ctx.send("❌ Erreur lors du unmute.")
        return
 
    await ctx.send(f"🔊 {membre.mention} n'est plus mute.")
 
 
# ================================================================
#                       SYSTÈME DE BAN
# ================================================================
#
# +ban @membre [raison]  : bannit un membre du serveur (réservé aux Gérants).
# +unban <user_id> [raison] : débannit un utilisateur par son ID (un membre
#                              banni n'est plus dans le serveur, donc pas de
#                              mention possible, il faut son ID numérique).

@bot.command(name="ban")
async def ban_command(ctx: commands.Context, membre: discord.Member = None, *, raison: str = None):
    if not is_gerant(ctx.author):
        await ctx.send("❌ Cette commande est réservée aux Gérants.")
        return

    if membre is None:
        await ctx.send("❌ Utilisation : `+ban @membre [raison]`")
        return

    if membre.id == ctx.author.id:
        await ctx.send("❌ Tu ne peux pas te bannir toi-même.")
        return
    if membre.top_role >= ctx.guild.me.top_role:
        await ctx.send("❌ Je ne peux pas bannir ce membre (son rôle est égal ou supérieur au mien).")
        return

    raison = raison or "Aucune raison précisée."

    try:
        await membre.send(f"🔨 Tu as été banni de **{ctx.guild.name}**.\nRaison : {raison}")
    except discord.HTTPException:
        pass  # DMs fermés : on bannit quand même, sans bloquer sur l'envoi du message.

    try:
        await ctx.guild.ban(membre, reason=f"{raison} (par {ctx.author})", delete_message_seconds=0)
    except discord.Forbidden:
        await ctx.send("❌ Je n'ai pas la permission de bannir ce membre.")
        return
    except discord.HTTPException:
        await ctx.send("❌ Erreur lors du bannissement.")
        return

    embed = discord.Embed(title="🔨 Membre banni", color=discord.Color.dark_red())
    embed.add_field(name="Membre", value=f"{membre.mention} ({membre.id})", inline=True)
    embed.add_field(name="Modérateur", value=ctx.author.mention, inline=True)
    embed.add_field(name="Raison", value=raison, inline=False)
    embed.set_thumbnail(url=membre.display_avatar.url)
    await ctx.send(embed=embed)


@bot.command(name="unban")
async def unban_command(ctx: commands.Context, user_id: str = None, *, raison: str = None):
    if not is_gerant(ctx.author):
        await ctx.send("❌ Cette commande est réservée aux Gérants.")
        return

    if user_id is None:
        await ctx.send("❌ Utilisation : `+unban <ID utilisateur> [raison]` (visible dans la liste des bannis Discord).")
        return

    try:
        uid = int(user_id.strip("<@!>"))
    except ValueError:
        await ctx.send("❌ ID invalide. Utilise l'ID numérique du compte (pas de mention possible pour un membre banni).")
        return

    try:
        ban_entry = await ctx.guild.fetch_ban(discord.Object(id=uid))
    except discord.NotFound:
        await ctx.send("❌ Cet utilisateur n'est pas banni sur ce serveur.")
        return
    except discord.HTTPException:
        await ctx.send("❌ Erreur lors de la vérification du bannissement.")
        return

    raison = raison or "Aucune raison précisée."

    try:
        await ctx.guild.unban(ban_entry.user, reason=f"{raison} (par {ctx.author})")
    except discord.Forbidden:
        await ctx.send("❌ Je n'ai pas la permission de débannir ce membre.")
        return
    except discord.HTTPException:
        await ctx.send("❌ Erreur lors du débannissement.")
        return

    embed = discord.Embed(title="✅ Membre débanni", color=discord.Color.green())
    embed.add_field(name="Utilisateur", value=f"{ban_entry.user} ({ban_entry.user.id})", inline=True)
    embed.add_field(name="Modérateur", value=ctx.author.mention, inline=True)
    embed.add_field(name="Raison", value=raison, inline=False)
    await ctx.send(embed=embed)
 
 
# ---------------- +tempban (réservé aux Modérateurs et Gérants) ----------------
#
# Contrairement à +ban (permanent, Gérants uniquement), +tempban est accessible
# dès le niveau Modérateur : le membre est banni puis automatiquement débanni
# à l'expiration de la durée (vérifiée toutes les minutes en arrière-plan).

def get_tempbans(guild_id: int) -> dict:
    guild_conf = config.setdefault(str(guild_id), {})
    return guild_conf.setdefault("tempbans", {})


def save_tempbans(guild_id: int, tempbans: dict) -> None:
    guild_conf = config.setdefault(str(guild_id), {})
    guild_conf["tempbans"] = tempbans
    save_config(config)


@bot.command(name="tempban")
async def tempban_command(ctx: commands.Context, membre: discord.Member = None, duree: str = None, *, raison: str = None):
    if not is_mod(ctx.author):
        await ctx.send("❌ Cette commande est réservée aux Modérateurs et Gérants.")
        return

    if membre is None or duree is None:
        await ctx.send("❌ Utilisation : `+tempban @membre <durée ex: 10m/2h/1j/2w> [raison]`")
        return

    if membre.id == ctx.author.id:
        await ctx.send("❌ Tu ne peux pas te bannir toi-même.")
        return
    if membre.top_role >= ctx.guild.me.top_role:
        await ctx.send("❌ Je ne peux pas bannir ce membre (son rôle est égal ou supérieur au mien).")
        return

    secondes = parse_duration(duree)
    if secondes is None:
        await ctx.send("❌ Durée invalide. Utilise un format comme `10m`, `2h`, `1j`, `2w` (semaine).")
        return

    raison = raison or "Aucune raison précisée."
    fin = datetime.now(PARIS_TZ) + timedelta(seconds=secondes)

    try:
        await membre.send(
            f"🔨 Tu as été banni temporairement de **{ctx.guild.name}** pour {duree}.\nRaison : {raison}"
        )
    except discord.HTTPException:
        pass

    try:
        await ctx.guild.ban(membre, reason=f"{raison} (tempban par {ctx.author}, {duree})", delete_message_seconds=0)
    except discord.Forbidden:
        await ctx.send("❌ Je n'ai pas la permission de bannir ce membre.")
        return
    except discord.HTTPException:
        await ctx.send("❌ Erreur lors du bannissement.")
        return

    tempbans = get_tempbans(ctx.guild.id)
    tempbans[str(membre.id)] = {
        "unban_at": fin.isoformat(),
        "moderator_id": str(ctx.author.id),
        "raison": raison,
    }
    save_tempbans(ctx.guild.id, tempbans)

    embed = discord.Embed(title="⏱️ Membre banni temporairement", color=discord.Color.dark_orange())
    embed.add_field(name="Membre", value=f"{membre.mention} ({membre.id})", inline=True)
    embed.add_field(name="Modérateur", value=ctx.author.mention, inline=True)
    embed.add_field(name="Durée", value=duree, inline=True)
    embed.add_field(name="Raison", value=raison, inline=False)
    embed.set_footer(text=f"Débanni automatiquement le {fin.strftime('%d/%m/%Y à %H:%M')} (heure de Paris)")
    embed.set_thumbnail(url=membre.display_avatar.url)
    await ctx.send(embed=embed)


@tasks.loop(minutes=1)
async def check_tempbans_loop():
    now = datetime.now(PARIS_TZ)
    for guild in bot.guilds:
        tempbans = config.get(str(guild.id), {}).get("tempbans", {})
        for user_id_str, data in list(tempbans.items()):
            fin = _parse_dt(data.get("unban_at"))
            if not fin or now < fin:
                continue
            try:
                await guild.unban(discord.Object(id=int(user_id_str)), reason="Fin du bannissement temporaire (+tempban)")
            except (discord.NotFound, discord.HTTPException):
                pass
            del tempbans[user_id_str]
            save_tempbans(guild.id, tempbans)


@check_tempbans_loop.before_loop
async def before_check_tempbans_loop():
    await bot.wait_until_ready()
 
 
# ================================================================
#              SYSTÈME DE SIGNALEMENT (/report)
# ================================================================
#
# Permet à n'importe quel membre de signaler discrètement un autre membre
# au staff. Le signalement part directement dans un salon privé (visible
# uniquement par le staff), jamais publiquement dans le salon où la commande
# est utilisée.

def get_report_channel_id(guild_id: int):
    return config.get(str(guild_id), {}).get("report_config", {}).get("channel_id")


def add_report(guild_id: int, reporter_id: int, target_id: int, raison: str) -> None:
    guild_conf = config.setdefault(str(guild_id), {})
    reports = guild_conf.setdefault("reports", {})
    target_reports = reports.setdefault(str(target_id), [])
    target_reports.append({
        "reporter_id": str(reporter_id),
        "reason": raison,
        "date": datetime.utcnow().isoformat(),
    })
    save_config(config)


def get_reports(guild_id: int, target_id: int) -> list:
    return config.get(str(guild_id), {}).get("reports", {}).get(str(target_id), [])


report_group = app_commands.Group(name="report", description="Signale un membre au staff (discrètement)")


@report_group.command(name="config", description="[Staff] Définit le salon où arrivent les signalements")
@app_commands.describe(salon="Salon privé (visible seulement par le staff) où seront envoyés les signalements")
async def report_config_cmd(interaction: discord.Interaction, salon: discord.TextChannel):
    if not is_staff(interaction.user):
        await interaction.response.send_message(
            "❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True
        )
        return

    guild_conf = config.setdefault(str(interaction.guild.id), {})
    report_conf = guild_conf.setdefault("report_config", {})
    report_conf["channel_id"] = salon.id
    save_config(config)

    await interaction.response.send_message(
        f"✅ Les signalements seront désormais envoyés dans {salon.mention}. "
        "Pense à vérifier que ce salon n'est visible que par le staff !",
        ephemeral=True,
    )


@report_group.command(name="envoyer", description="Signale un membre au staff (discret, personne d'autre ne le voit)")
@app_commands.describe(membre="Le membre à signaler", raison="Explique la raison du signalement")
async def report_envoyer_cmd(interaction: discord.Interaction, membre: discord.Member, raison: str):
    if membre.id == interaction.user.id:
        await interaction.response.send_message("❌ Tu ne peux pas te signaler toi-même.", ephemeral=True)
        return
    if membre.bot:
        await interaction.response.send_message("❌ Tu ne peux pas signaler un bot.", ephemeral=True)
        return

    channel_id = get_report_channel_id(interaction.guild.id)
    if not channel_id:
        await interaction.response.send_message(
            "❌ Le système de signalement n'est pas configuré sur ce serveur. Un membre du staff doit "
            "utiliser `/report config` d'abord.",
            ephemeral=True,
        )
        return

    channel = interaction.guild.get_channel(channel_id)
    if channel is None:
        await interaction.response.send_message(
            "❌ Le salon de signalement configuré est introuvable. Contacte le staff.", ephemeral=True
        )
        return

    add_report(interaction.guild.id, interaction.user.id, membre.id, raison)

    embed = discord.Embed(
        title="🚩 Nouveau signalement",
        color=discord.Color.orange(),
        timestamp=datetime.utcnow(),
    )
    embed.add_field(name="Membre signalé", value=f"{membre.mention} ({membre})", inline=False)
    embed.add_field(name="Signalé par", value=f"{interaction.user.mention} ({interaction.user})", inline=False)
    embed.add_field(name="Raison", value=raison, inline=False)
    embed.add_field(name="Salon d'origine", value=interaction.channel.mention if interaction.channel else "—", inline=False)

    try:
        await channel.send(embed=embed)
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ Je n'ai pas la permission d'écrire dans le salon de signalement configuré.", ephemeral=True
        )
        return
    except discord.HTTPException:
        await interaction.response.send_message("❌ Erreur lors de l'envoi du signalement.", ephemeral=True)
        return

    await interaction.response.send_message(
        "✅ Ton signalement a bien été envoyé au staff. Merci de nous aider à garder le serveur sain !",
        ephemeral=True,
    )


@report_group.command(name="historique", description="[Staff] Affiche les signalements reçus contre un membre")
@app_commands.describe(membre="Le membre dont tu veux voir l'historique des signalements")
async def report_historique_cmd(interaction: discord.Interaction, membre: discord.Member):
    if not is_staff(interaction.user):
        await interaction.response.send_message(
            "❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True
        )
        return

    reports = get_reports(interaction.guild.id, membre.id)
    if not reports:
        embed = discord.Embed(
            title=f"🚩 Signalements de {membre.display_name}",
            description="Aucun signalement.",
            color=discord.Color.green(),
        )
    else:
        lignes = []
        for i, r in enumerate(reports, start=1):
            date = r["date"][:10]
            reporter = interaction.guild.get_member(int(r["reporter_id"]))
            nom_reporter = reporter.mention if reporter else f"<@{r['reporter_id']}>"
            lignes.append(f"**#{i}** — {r['reason']} *(signalé par {nom_reporter}, le {date})*")
        embed = discord.Embed(
            title=f"🚩 Signalements de {membre.display_name}",
            description="\n".join(lignes),
            color=discord.Color.orange(),
        )
    embed.set_footer(text=f"{len(reports)} signalement(s)")
    embed.set_thumbnail(url=membre.display_avatar.url)
    await interaction.response.send_message(embed=embed, ephemeral=True)


bot.tree.add_command(report_group)
 
 
# ---------------- +add role / +remove role ----------------
 
@bot.group(name="add", invoke_without_command=True)
async def add_group(ctx: commands.Context):
    await ctx.send("❌ Utilise `+add role @membre @role`.")
 
 
@add_group.command(name="role")
async def add_role_cmd(ctx: commands.Context, membre: discord.Member, *, role: discord.Role):
    if not is_gerant(ctx.author):
        await ctx.send("❌ Cette commande est réservée aux Gérants.")
        return
 
    if role >= ctx.guild.me.top_role:
        await ctx.send("❌ Je ne peux pas attribuer un rôle égal ou supérieur à mon rôle le plus haut.")
        return
 
    try:
        await membre.add_roles(role, reason=f"Ajouté par {ctx.author}")
    except discord.Forbidden:
        await ctx.send("❌ Permissions insuffisantes pour ajouter ce rôle.")
        return
    except discord.HTTPException:
        await ctx.send("❌ Erreur lors de l'ajout du rôle.")
        return
 
    await ctx.send(f"✅ Le rôle {role.mention} a été ajouté à {membre.mention}.")
 
 
@bot.group(name="remove", invoke_without_command=True)
async def remove_group(ctx: commands.Context):
    await ctx.send("❌ Utilise `+remove role @membre @role`.")
 
 
@remove_group.command(name="role")
async def remove_role_cmd(ctx: commands.Context, membre: discord.Member, *, role: discord.Role):
    if not is_gerant(ctx.author):
        await ctx.send("❌ Cette commande est réservée aux Gérants.")
        return
 
    if role >= ctx.guild.me.top_role:
        await ctx.send("❌ Je ne peux pas retirer un rôle égal ou supérieur à mon rôle le plus haut.")
        return
 
    try:
        await membre.remove_roles(role, reason=f"Retiré par {ctx.author}")
    except discord.Forbidden:
        await ctx.send("❌ Permissions insuffisantes pour retirer ce rôle.")
        return
    except discord.HTTPException:
        await ctx.send("❌ Erreur lors du retrait du rôle.")
        return
 
    await ctx.send(f"✅ Le rôle {role.mention} a été retiré à {membre.mention}.")
 
 
# ---------------- +monrole (auto-attribution via permission Gérer les rôles) ----------------
#
# N'importe qui possédant la permission Discord "Gérer les rôles" (ou l'ID
# ci-dessous explicitement) peut s'attribuer N'IMPORTE QUEL rôle du serveur,
# même un rôle normalement placé au-dessus du sien. Ça fonctionne car Discord
# ne limite pas un BOT par la hiérarchie de la personne qui tape la commande,
# seulement par la position du rôle du BOT lui-même : tant que le rôle du bot
# est plus haut que le rôle demandé, l'attribution passe.
#
# ⚠️ Cette commande donne un pouvoir important (privilège d'auto-attribution
# de rôle). Ne la laisse accessible qu'aux personnes de confiance.

ROLE_SELF_ASSIGN_USER_IDS = {1212326779591725079}


def peut_sauto_attribuer_un_role(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    if member.guild_permissions.manage_roles:
        return True
    return member.id in ROLE_SELF_ASSIGN_USER_IDS


@bot.command(name="monrole")
async def monrole_command(ctx: commands.Context, *, role: discord.Role = None):
    if not peut_sauto_attribuer_un_role(ctx.author):
        await ctx.send("❌ Tu dois avoir la permission **Gérer les rôles** pour utiliser cette commande.")
        return

    if role is None:
        await ctx.send("❌ Utilisation : `+monrole @role` (ou le nom exact du rôle).")
        return

    if role >= ctx.guild.me.top_role:
        await ctx.send(
            "❌ Je ne peux pas attribuer ce rôle : il est placé au-dessus (ou au même niveau) que mon rôle le "
            "plus haut. Fais remonter mon rôle dans **Paramètres du serveur → Rôles**."
        )
        return

    if role in ctx.author.roles:
        await ctx.send(f"⚠️ Tu as déjà le rôle {role.mention}.")
        return

    try:
        await ctx.author.add_roles(role, reason=f"Auto-attribution via +monrole (permission Gérer les rôles)")
    except discord.Forbidden:
        await ctx.send("❌ Permissions insuffisantes pour t'attribuer ce rôle.")
        return
    except discord.HTTPException:
        await ctx.send("❌ Erreur lors de l'attribution du rôle.")
        return

    embed = discord.Embed(
        title="✅ Rôle auto-attribué",
        description=f"Tu t'es donné le rôle {role.mention} grâce à la permission du bot.",
        color=discord.Color.blurple(),
    )
    embed.set_footer(text=f"Demandé par {ctx.author}")
    await ctx.send(embed=embed)
 
 
# ================================================================
#                    SYSTÈME ANTI-SPAM
# ================================================================
#
# Si un membre envoie 3 fois de suite le même message (dans une fenêtre de
# 60 secondes), le bot l'avertit. Le compteur est en mémoire (pas persisté
# dans config.json), il se remet donc à zéro si le bot redémarre.

SPAM_WINDOW_SECONDS = 60
SPAM_THRESHOLD = 3

spam_tracker: dict[tuple[int, int], dict] = {}  # (guild_id, user_id) -> {"content", "count", "last_time"}


async def check_spam(message: discord.Message) -> None:
    contenu = message.content.strip().lower()
    if not contenu:
        return

    cle = (message.guild.id, message.author.id)
    maintenant = datetime.now(PARIS_TZ)
    entree = spam_tracker.get(cle)

    if entree and entree["content"] == contenu and (maintenant - entree["last_time"]).total_seconds() <= SPAM_WINDOW_SECONDS:
        entree["count"] += 1
        entree["last_time"] = maintenant
    else:
        entree = {"content": contenu, "count": 1, "last_time": maintenant}

    spam_tracker[cle] = entree

    if entree["count"] >= SPAM_THRESHOLD:
        entree["count"] = 0  # évite de ré-avertir à chaque nouveau message identique
        try:
            await message.channel.send(
                f"⚠️ {message.author.mention}, merci d'éviter d'envoyer plusieurs fois le même message (anti-spam)."
            )
        except discord.HTTPException:
            pass


# ================================================================
#                          on_message
# ================================================================
 


async def add_guild_message_xp(message: discord.Message) -> None:
    """Ajoute de l'XP (et des points de classement) à la guilde du membre avec
    un cooldown anti-farm. classement_points est un compteur totalement
    indépendant de xp/level, réinitialisable via /guilde classement reset."""
    if message.guild is None or message.author.bot:
        return

    guild_id_value, guild_data = find_member_guild(message.guild.id, message.author.id)
    if not guild_data:
        return

    # Une guilde non vérifiée peut être créée, mais son activité ne progresse pas
    # tant qu'elle n'a pas été validée par le staff.
    if not guild_data.get("verified"):
        return

    # Guilde sous l'effet d'un +block guild : aucun gain d'XP ni de points.
    if is_guild_blocked(guild_data):
        return

    now = datetime.now(PARIS_TZ)
    server_cooldowns = GUILD_XP_COOLDOWNS.setdefault(message.guild.id, {})
    last_gain = server_cooldowns.get(message.author.id)
    if last_gain and (now - last_gain).total_seconds() < GUILD_XP_COOLDOWN_SECONDS:
        return

    server_cooldowns[message.author.id] = now
    old_level = int(guild_data.get("level", 1))
    old_max = guild_max_members(old_level)

    # Rendements décroissants : plus la guilde a de membres, moins chaque
    # message rapporte d'XP individuellement.
    gain_xp = guild_xp_gain_for_size(len(guild_data.get("members", [])))
    if is_guild_xp_fast(guild_data):
        gain_xp *= XP_FAST_MULTIPLIER
    guild_data["xp"] = int(guild_data.get("xp", 0)) + gain_xp

    # Points de classement : volontairement plus durs à obtenir que l'XP/niveau.
    # Un seul gain sur GUILD_CLASSEMENT_HARD_INTERVAL messages valides rapporte des points.
    guild_data["classement_msg_counter"] = int(guild_data.get("classement_msg_counter", 0)) + 1
    if guild_data["classement_msg_counter"] >= GUILD_CLASSEMENT_HARD_INTERVAL:
        guild_data["classement_msg_counter"] = 0
        guild_data["classement_points"] = int(guild_data.get("classement_points", 0)) + GUILD_CLASSEMENT_POINTS_PER_TICK

    new_level = guild_level_from_xp(guild_data["xp"])
    guild_data["level"] = new_level
    guild_data["max_members"] = guild_max_members(new_level)
    if new_level >= GUILD_RENAME_LEVEL and old_level < GUILD_RENAME_LEVEL:
        guild_data["rename_available"] = True
    save_server_guilds(message.guild.id, get_server_guilds(message.guild.id))

    if new_level > old_level:
        # La guilde gagne de l'argent pour chaque niveau passé (utilisable en boutique).
        gain_argent = (new_level - old_level) * GUILD_MONEY_PER_LEVEL
        guild_data["argent"] = int(guild_data.get("argent", 0)) + gain_argent
        guild_data["argent_total"] = int(guild_data.get("argent_total", 0)) + gain_argent
        save_server_guilds(message.guild.id, get_server_guilds(message.guild.id))

        # Notification discrète uniquement lors d'un niveau gagné.
        try:
            await message.channel.send(
                f"🎉 **{guild_data['name']}** passe au **niveau {new_level}** ! (+{gain_argent}$ 💰)",
                delete_after=8,
            )
            if new_level == GUILD_CAPACITY_LEVEL and old_max < GUILD_MAX_MEMBERS:
                await message.channel.send(
                    f"👥 La capacité de **{guild_data['name']}** passe maintenant à **{GUILD_MAX_MEMBERS} membres** !",
                    delete_after=8,
                )
        except discord.HTTPException:
            pass

        # Niveau 10 : rôle exclusif aux membres, portant le nom de la guilde.
        if new_level >= GUILD_EXCLUSIVE_ROLE_LEVEL and old_level < GUILD_EXCLUSIVE_ROLE_LEVEL:
            role = await ensure_guild_exclusive_role(message.guild, guild_data)
            save_server_guilds(message.guild.id, get_server_guilds(message.guild.id))
            if role:
                try:
                    await message.channel.send(
                        f"🔰 **{guild_data['name']}** débloque son rôle exclusif : {role.mention} !",
                        delete_after=10,
                    )
                except discord.HTTPException:
                    pass

        # Niveau 20 : salon privé réservé aux membres de la guilde.
        if new_level >= GUILD_EXCLUSIVE_CHANNEL_LEVEL and old_level < GUILD_EXCLUSIVE_CHANNEL_LEVEL:
            channel = await ensure_guild_exclusive_channel(message.guild, guild_data)
            save_server_guilds(message.guild.id, get_server_guilds(message.guild.id))
            if channel:
                try:
                    await message.channel.send(
                        f"🏰 **{guild_data['name']}** débloque son salon privé : {channel.mention} !",
                        delete_after=10,
                    )
                except discord.HTTPException:
                    pass

    # Vérifie si cette progression (XP, argent, points de classement) vient de
    # remplir l'objectif d'une des quêtes de guilde actives sur le serveur.
    try:
        await check_and_complete_quetes(message.guild)
    except discord.HTTPException:
        pass


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
 
    if message.guild is not None:
        await check_spam(message)
        await check_flood(message)
        await add_guild_message_xp(message)
        # Comptage pour l'Élu de la semaine
        bump_weekly_count(message.guild.id, message.author.id)
 
    if message.content.lower().strip() == "+concept list reset":
        if not is_staff(message.author):
            await message.channel.send("❌ Cette commande est réservée au staff.")
            return
 
        save_concept_list(message.guild.id, [])
        await message.channel.send("✅ La **liste Concept** a été entièrement réinitialisée.")
        return
 
    await bot.process_commands(message)
 
 
# ================================================================
#                    GESTION GLOBALE DES ERREURS
# ================================================================
#
# Évite que des erreurs comme "403 Forbidden : permissions manquantes"
# fassent planter une commande sans explication claire, et permet de
# prévenir la personne concernée plutôt que de simplement écrire dans les logs.
 
@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.CommandNotFound):
        return
 
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Argument manquant : `{error.param.name}`. Vérifie la syntaxe avec `+cmds`.")
        return
 
    if isinstance(error, (commands.BadArgument, commands.MemberNotFound, commands.RoleNotFound)):
        await ctx.send("❌ Argument invalide (membre, rôle ou valeur introuvable). Vérifie ta commande.")
        return
 
    original = getattr(error, "original", error)
 
    if isinstance(original, discord.Forbidden):
        salon_nom = ctx.channel.mention if hasattr(ctx.channel, "mention") else str(ctx.channel)
        avertissement = (
            f"❌ Je n'ai pas les permissions nécessaires pour exécuter `{ctx.command}` dans {salon_nom} "
            f"sur **{ctx.guild.name if ctx.guild else 'ce serveur'}**.\n"
            "Un membre du staff doit vérifier que mon rôle a bien les permissions "
            "**Envoyer des messages**, **Intégrer des liens** et **Gérer les salons/rôles** (selon la commande)."
        )
        try:
            await ctx.send(avertissement)
        except discord.Forbidden:
            # Impossible d'écrire même le message d'erreur dans ce salon : on prévient en DM.
            try:
                await ctx.author.send(avertissement)
            except discord.HTTPException:
                pass
        return
 
    print(f"⚠️ Erreur non gérée dans la commande '{ctx.command}': {error}")
 
 
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    original = getattr(error, "original", error)
 
    if isinstance(original, discord.Forbidden):
        message = (
            "❌ Je n'ai pas les permissions nécessaires pour exécuter cette action ici "
            "(vérifie mes permissions dans ce salon/cette catégorie)."
        )
    elif isinstance(error, app_commands.MissingPermissions):
        message = "❌ Tu n'as pas la permission d'utiliser cette commande."
    else:
        message = "❌ Une erreur est survenue lors de l'exécution de cette commande."
        print(f"⚠️ Erreur non gérée dans la commande slash '{interaction.command}': {error}")
 
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        pass
 
 # ================================================================
#                    STATUT PERSONNALISÉ DU BOT
# ================================================================

STATUT_ROTATION_SECONDS = 15

STATUTS = [
    "Moderations | +cmds staff",
    "👀 {membres} membres sur le serveur",
]

_cycle_statuts = itertools.cycle(STATUTS)


@tasks.loop(seconds=STATUT_ROTATION_SECONDS)
async def rotation_statut():
    guild = bot.guilds[0] if bot.guilds else None
    membres = guild.member_count if guild else "?"
    texte = next(_cycle_statuts).format(membres=membres)

    await bot.change_presence(
        activity=discord.CustomActivity(name=texte),
        status=discord.Status.online,
    )


@rotation_statut.before_loop
async def before_rotation_statut():
    await bot.wait_until_ready()

# ================================================================
#                       SYSTÈME DE GIVEAWAYS
# ================================================================
#
# Le staff lance un giveaway avec /giveaway start : lot, durée, nombre de
# gagnants, et un emoji au choix (classique 🎉 ou emoji personnalisé du
# serveur <:nom:id>) pour participer. Les membres réagissent avec cet emoji
# sur le message pour s'inscrire. À l'expiration, un ou plusieurs gagnants
# sont tirés au hasard parmi les membres ayant réagi.
#
# Persistant : les giveaways en cours sont stockés dans config.json, donc
# un redémarrage du bot ne les fait pas disparaître (le message est
# re-récupéré via son ID au moment du tirage).
#
# NOTE D'INTÉGRATION :
# - Colle ce bloc avant la section "EVENTS" de ton script.
# - Dans `on_ready`, ajoute :
#       if not check_giveaways.is_running():
#           check_giveaways.start()
# - Réutilise tes fonctions déjà existantes : parse_duration, _parse_dt,
#   PARIS_TZ, is_staff, save_config, config.

GIVEAWAY_CHECK_INTERVAL_MINUTES = 1
GIVEAWAY_EMOJI_DEFAUT = "🎉"


# ---------------- Emoji ----------------

def parse_emoji_giveaway(guild: discord.Guild, emoji_str: str):
    """Convertit la saisie en emoji utilisable pour réagir : soit un emoji
    unicode classique, soit un emoji personnalisé du serveur (<:nom:id>).
    Retourne None si l'emoji personnalisé demandé n'existe pas sur ce serveur."""
    emoji_str = emoji_str.strip()
    match = re.match(r"^<a?:\w+:(\d+)>$", emoji_str)
    if match:
        emoji_id = int(match.group(1))
        return discord.utils.get(guild.emojis, id=emoji_id)
    return emoji_str  # emoji unicode classique (ex : 🎉, 🔥, 🍀...)


# ---------------- Embeds ----------------

def build_giveaway_embed(prix: str, fin: datetime, nb_gagnants: int, emoji, host: discord.abc.User, termine: bool = False) -> discord.Embed:
    embed = discord.Embed(
        title="🎉 GIVEAWAY TERMINÉ 🎉" if termine else "🎉 GIVEAWAY 🎉",
        description=(
            f"🎁 **Lot :** {prix}\n"
            f"🏆 **Nombre de gagnants :** {nb_gagnants}\n"
            f"👤 **Organisé par :** {host.mention}\n"
            f"⏰ **Fin :** <t:{int(fin.timestamp())}:R>\n\n"
            + ("Ce giveaway est terminé." if termine else f"Réagis avec {emoji} pour participer !")
        ),
        color=discord.Color.dark_grey() if termine else discord.Color.fuchsia(),
    )
    return embed


def build_resultat_embed(prix: str, gagnants: list) -> discord.Embed:
    if gagnants:
        mentions = ", ".join(g.mention for g in gagnants)
        description = f"Félicitations {mentions} ! Vous remportez **{prix}** ! 🎉"
        couleur = discord.Color.gold()
    else:
        description = f"😢 Personne n'a participé au giveaway **{prix}**... Aucun gagnant."
        couleur = discord.Color.dark_grey()

    return discord.Embed(title="🏆 Résultat du giveaway", description=description, color=couleur)


# ---------------- Tirage ----------------

async def recuperer_participants(message: discord.Message, emoji_str: str) -> list:
    """Retourne la liste des membres (hors bots) ayant réagi avec l'emoji du giveaway."""
    for reaction in message.reactions:
        if str(reaction.emoji) == emoji_str:
            participants = []
            async for user in reaction.users():
                if not user.bot:
                    participants.append(user)
            return participants
    return []


async def terminer_giveaway(guild: discord.Guild, message_id: str, data: dict) -> None:
    channel = guild.get_channel(data["channel_id"])
    if channel is None:
        data["termine"] = True
        return

    try:
        message = await channel.fetch_message(int(message_id))
    except (discord.NotFound, discord.HTTPException):
        data["termine"] = True
        return

    participants = await recuperer_participants(message, data["emoji"])
    nb_gagnants = min(data["winners_count"], len(participants))
    gagnants = random.sample(participants, nb_gagnants) if nb_gagnants > 0 else []

    data["termine"] = True
    data["gagnants_ids"] = [g.id for g in gagnants]

    # Met à jour le message original pour indiquer qu'il est terminé.
    try:
        fin = _parse_dt(data["end_at"])
        host = guild.get_member(data["host_id"])
        embed_original = build_giveaway_embed(data["prize"], fin, data["winners_count"], data["emoji"], host or guild.me, termine=True)
        await message.edit(embed=embed_original)
    except discord.HTTPException:
        pass

    try:
        await channel.send(embed=build_resultat_embed(data["prize"], gagnants))
    except discord.HTTPException:
        pass


@tasks.loop(minutes=GIVEAWAY_CHECK_INTERVAL_MINUTES)
async def check_giveaways():
    now = datetime.now(PARIS_TZ)
    for guild in bot.guilds:
        guild_conf = config.get(str(guild.id), {})
        giveaways = guild_conf.get("giveaways", {})
        if not giveaways:
            continue

        changed = False
        for message_id, data in list(giveaways.items()):
            if data.get("termine"):
                continue
            fin = _parse_dt(data.get("end_at"))
            if not fin or now < fin:
                continue
            await terminer_giveaway(guild, message_id, data)
            changed = True

        if changed:
            guild_conf["giveaways"] = giveaways
            save_config(config)


@check_giveaways.before_loop
async def before_check_giveaways():
    await bot.wait_until_ready()


# ---------------- Commandes ----------------

giveaway_group = app_commands.Group(name="giveaway", description="Système de giveaways")


@giveaway_group.command(name="start", description="[Staff] Démarre un giveaway")
@app_commands.describe(
    prix="Le lot à gagner",
    duree="Durée du giveaway (ex : 10m, 2h, 1j)",
    gagnants="Nombre de gagnants (1 par défaut)",
    emoji="Emoji pour participer (🎉 par défaut, ou un emoji personnalisé du serveur)",
)
async def giveaway_start_cmd(
    interaction: discord.Interaction,
    prix: str,
    duree: str,
    gagnants: app_commands.Range[int, 1, 20] = 1,
    emoji: str = GIVEAWAY_EMOJI_DEFAUT,
):
    if not is_staff(interaction.user):
        await interaction.response.send_message("❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True)
        return

    secondes = parse_duration(duree)
    if secondes is None:
        await interaction.response.send_message("❌ Durée invalide. Utilise un format comme `10m`, `2h`, `1j`.", ephemeral=True)
        return

    emoji_valide = parse_emoji_giveaway(interaction.guild, emoji)
    if emoji_valide is None:
        await interaction.response.send_message("❌ Cet emoji personnalisé n'existe pas sur ce serveur.", ephemeral=True)
        return

    fin = datetime.now(PARIS_TZ) + timedelta(seconds=secondes)
    embed = build_giveaway_embed(prix, fin, gagnants, emoji_valide, interaction.user)

    await interaction.response.send_message("✅ Giveaway lancé !", ephemeral=True)
    message = await interaction.channel.send(embed=embed)

    try:
        await message.add_reaction(emoji_valide)
    except discord.HTTPException:
        await interaction.followup.send(
            "⚠️ Giveaway créé, mais je n'ai pas pu réagir avec cet emoji (vérifie que je peux l'utiliser).",
            ephemeral=True,
        )

    guild_conf = config.setdefault(str(interaction.guild.id), {})
    giveaways = guild_conf.setdefault("giveaways", {})
    giveaways[str(message.id)] = {
        "channel_id": interaction.channel.id,
        "prize": prix,
        "winners_count": gagnants,
        "emoji": str(emoji_valide),
        "end_at": fin.isoformat(),
        "host_id": interaction.user.id,
        "termine": False,
    }
    save_config(config)


@giveaway_group.command(name="end", description="[Staff] Termine immédiatement un giveaway et tire les gagnants")
@app_commands.describe(message_id="L'ID du message du giveaway à terminer")
async def giveaway_end_cmd(interaction: discord.Interaction, message_id: str):
    if not is_staff(interaction.user):
        await interaction.response.send_message("❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True)
        return

    guild_conf = config.setdefault(str(interaction.guild.id), {})
    giveaways = guild_conf.setdefault("giveaways", {})
    data = giveaways.get(message_id.strip())
    if not data:
        await interaction.response.send_message("❌ Giveaway introuvable (vérifie l'ID du message).", ephemeral=True)
        return
    if data.get("termine"):
        await interaction.response.send_message("❌ Ce giveaway est déjà terminé.", ephemeral=True)
        return

    await interaction.response.send_message("✅ Giveaway terminé, tirage en cours...", ephemeral=True)
    await terminer_giveaway(interaction.guild, message_id.strip(), data)
    save_config(config)


@giveaway_group.command(name="reroll", description="[Staff] Retire de nouveaux gagnants pour un giveaway déjà terminé")
@app_commands.describe(message_id="L'ID du message du giveaway concerné")
async def giveaway_reroll_cmd(interaction: discord.Interaction, message_id: str):
    if not is_staff(interaction.user):
        await interaction.response.send_message("❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True)
        return

    guild_conf = config.get(str(interaction.guild.id), {})
    giveaways = guild_conf.get("giveaways", {})
    data = giveaways.get(message_id.strip())
    if not data or not data.get("termine"):
        await interaction.response.send_message("❌ Giveaway introuvable ou pas encore terminé.", ephemeral=True)
        return

    channel = interaction.guild.get_channel(data["channel_id"])
    if channel is None:
        await interaction.response.send_message("❌ Le salon d'origine est introuvable.", ephemeral=True)
        return

    try:
        message = await channel.fetch_message(int(message_id.strip()))
    except (discord.NotFound, discord.HTTPException):
        await interaction.response.send_message("❌ Le message du giveaway est introuvable.", ephemeral=True)
        return

    participants = await recuperer_participants(message, data["emoji"])
    anciens_gagnants = set(data.get("gagnants_ids", []))
    nouveaux_candidats = [p for p in participants if p.id not in anciens_gagnants] or participants

    nb_gagnants = min(data["winners_count"], len(nouveaux_candidats))
    gagnants = random.sample(nouveaux_candidats, nb_gagnants) if nb_gagnants > 0 else []

    data["gagnants_ids"] = [g.id for g in gagnants]
    save_config(config)

    await interaction.response.send_message("✅ Nouveau tirage effectué !", ephemeral=True)
    await channel.send(embed=build_resultat_embed(f"{data['prize']} (reroll)", gagnants))


@giveaway_group.command(name="liste", description="Affiche les giveaways actuellement en cours")
async def giveaway_liste_cmd(interaction: discord.Interaction):
    guild_conf = config.get(str(interaction.guild.id), {})
    giveaways = guild_conf.get("giveaways", {})
    en_cours = [(mid, d) for mid, d in giveaways.items() if not d.get("termine")]

    if not en_cours:
        await interaction.response.send_message("Aucun giveaway en cours actuellement.", ephemeral=True)
        return

    lignes = []
    for message_id, data in en_cours:
        fin = _parse_dt(data.get("end_at"))
        lignes.append(
            f"🎁 **{data['prize']}** — {data['winners_count']} gagnant(s) — <t:{int(fin.timestamp())}:R> "
            f"(ID : `{message_id}`)"
        )

    embed = discord.Embed(title="🎉 Giveaways en cours", description="\n".join(lignes), color=discord.Color.fuchsia())
    await interaction.response.send_message(embed=embed, ephemeral=True)


bot.tree.add_command(giveaway_group)

# ================================================================
#                          EVENTS
# ================================================================
 
@bot.event
async def on_ready():
    bot.add_view(AbsenceView())
    bot.add_view(TicketCloseView())

    # Reconstruit les panneaux de tickets existants pour que les boutons
    # restent fonctionnels après un redémarrage du bot.
    for guild_id_str, guild_conf in config.items():
        for panel_id, panel_data in guild_conf.get("ticket_panels", {}).items():
            try:
                bot.add_view(build_ticket_panel_view(panel_id, panel_data["buttons"]))
            except Exception as e:
                print(f"Erreur lors de la reconstruction du panneau de tickets {panel_id} : {e}")

    try:
        guild_obj = discord.Object(id=DEV_GUILD_ID)

        # 1) On copie les commandes (définies globalement dans le code) vers le
        #    serveur de dev, puis on les synchronise dessus (quasi instantané).
        bot.tree.copy_global_to(guild=guild_obj)
        synced = await bot.tree.sync(guild=guild_obj)
        print(f"{len(synced)} commande(s) slash synchronisée(s) sur le serveur de dev.")

        # 2) On vide ensuite la liste des commandes GLOBALES côté Discord.
        #    Sans cette étape, si une synchro globale a déjà eu lieu une fois
        #    (ex: `bot.tree.sync()` sans guild), Discord affiche chaque
        #    commande en double (une version globale + une version serveur).
        bot.tree.clear_commands(guild=None)
        await bot.tree.sync()
        print("Commandes globales nettoyées (évite les doublons dans /).")
    except Exception as e:
        print(f"Erreur de synchronisation : {e}")
 
    for guild in bot.guilds:
        await update_invites_cache(guild)
 
    if not update_stats_loop.is_running():
        update_stats_loop.start()
 
    if not check_elu_semaine.is_running():
        check_elu_semaine.start()
 
    if not check_anniversaires.is_running():
        check_anniversaires.start()

    if not check_tiktok_loop.is_running():
        check_tiktok_loop.start()

    if not check_animal_spawns.is_running():
        check_animal_spawns.start()

    if not check_quetes_loop.is_running():
        check_quetes_loop.start()

    if not check_tempbans_loop.is_running():
        check_tempbans_loop.start()

    if not check_giveaways.is_running():
        check_giveaways.start()

    if not rotation_statut.is_running():
        rotation_statut.start()

    print(f"✅ Connecté en tant que {bot.user}")
 
if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_TOKEN")
    if not TOKEN:
        raise RuntimeError("Défini la variable d'environnement DISCORD_TOKEN avant de lancer le bot.")
    bot.run(TOKEN)
