"""ProTanki nickname generator and account tracker.

Generates meaningful English-word nicknames across themed categories
and persists registration state in the bot's SQLite database.
"""

from __future__ import annotations

import random

WORD_LISTS: dict[str, list[str]] = {
    "nature": [
        "Flower", "Forest", "Meadow", "Garden", "Valley", "Canyon", "Glacier",
        "Aurora", "Breeze", "Sunset", "Sunrise", "Eclipse", "Comet", "Nebula",
        "Crystal", "Diamond", "Emerald", "Amber", "Pearl", "Quartz",
        "River", "Ocean", "Storm", "Thunder", "Blizzard", "Tornado", "Typhoon",
        "Volcano", "Island", "Desert", "Tundra", "Savanna", "Prairie", "Summit",
        "Cascade", "Lagoon", "Horizon", "Zenith", "Equinox",
        "Solstice", "Monsoon", "Tempest", "Avalanche", "Inferno", "Mirage",
        "Oasis", "Cosmos", "Galaxy", "Stellar", "Lunar", "Solar", "Astral",
        "Blossom", "Petal", "Orchid", "Lotus", "Jasmine", "Dahlia", "Violet",
        "Sakura", "Willow", "Maple", "Cedar", "Bamboo", "Sequoia", "Cypress",
    ],
    "animals": [
        "Falcon", "Eagle", "Hawk", "Phoenix", "Raven", "Sparrow", "Robin",
        "Panther", "Jaguar", "Leopard", "Tiger", "Cobra", "Viper",
        "Dragon", "Griffin", "Pegasus", "Hydra", "Kraken", "Leviathan",
        "Wolf", "Otter", "Badger", "Ferret", "Marten",
        "Shark", "Whale", "Dolphin", "Marlin", "Barracuda", "Piranha",
        "Mustang", "Stallion", "Raptor", "Condor", "Osprey", "Pelican",
        "Scorpion", "Mantis", "Hornet", "Beetle", "Monarch", "Firefly",
        "Cheetah", "Gazelle", "Impala", "Bison", "Mammoth", "Rhino",
        "Iguana", "Chameleon", "Salamander", "Triton",
    ],
    "food": [
        "Banana", "Mango", "Papaya", "Cherry", "Coconut", "Lemon", "Melon",
        "Pepper", "Ginger", "Vanilla", "Cinnamon", "Saffron", "Wasabi",
        "Cookie", "Waffle", "Pancake", "Brownie", "Muffin", "Pretzel",
        "Truffle", "Caramel", "Toffee", "Sorbet", "Gelato",
        "Avocado", "Pistachio", "Almond", "Cashew", "Walnut", "Pecan",
        "Olive", "Basil", "Thyme", "Clover", "Fennel", "Cumin",
        "Honey", "Nectar", "Syrup", "Butter", "Cheese", "Yogurt",
        "Raisin", "Apricot", "Guava", "Lychee", "Paprika",
    ],
    "colors": [
        "Crimson", "Scarlet", "Maroon", "Coral", "Salmon", "Amber",
        "Golden", "Bronze", "Copper", "Ivory", "Silver", "Platinum",
        "Azure", "Cobalt", "Indigo", "Cerulean", "Teal",
        "Emerald", "Jade", "Olive", "Sage",
        "Violet", "Orchid", "Magenta", "Fuchsia", "Lilac", "Mauve",
        "Onyx", "Ebony", "Obsidian", "Charcoal", "Slate", "Pewter",
    ],
    "space": [
        "Quasar", "Pulsar", "Meteor", "Asteroid", "Saturn", "Jupiter",
        "Neptune", "Mercury", "Venus", "Pluto", "Orion", "Andromeda",
        "Sirius", "Polaris", "Rigel", "Altair", "Arcturus",
        "Cassini", "Kepler", "Hubble", "Galileo", "Voyager", "Apollo",
        "Titan", "Europa", "Triton", "Ganymede", "Callisto", "Phobos",
        "Cosmos", "Nebula", "Photon", "Proton", "Neutron", "Plasma",
    ],
    "mythology": [
        "Athena", "Apollo", "Hermes", "Artemis", "Hades", "Poseidon",
        "Thor", "Freya", "Fenrir",
        "Anubis", "Osiris", "Sphinx", "Pharaoh", "Scarab",
        "Samurai", "Shogun", "Ronin", "Ninja", "Sensei",
        "Valkyrie", "Viking", "Spartan", "Centurion", "Gladiator",
        "Paladin", "Knight", "Templar", "Crusader", "Sentinel",
        "Oracle", "Mystic", "Wizard", "Sorcerer", "Druid", "Shaman",
    ],
    "tech": [
        "Quantum", "Binary", "Vector", "Matrix", "Cipher", "Pixel",
        "Glitch", "Vertex", "Vortex", "Nexus", "Helix", "Prism",
        "Syntax", "Logic", "Kernel", "Daemon", "Socket", "Module",
        "Crypto", "Laser", "Plasma", "Fusion", "Reactor",
        "Turbo", "Nitro", "Boost", "Blitz", "Flash", "Spark",
        "Chrome", "Titan", "Atlas", "Omega", "Sigma", "Delta",
    ],
    "weather": [
        "Frost", "Icicle", "Hailstone", "Snowflake", "Winter", "Autumn",
        "Spring", "Summer", "Breeze", "Cyclone", "Hurricane",
        "Lightning", "Rumble", "Drizzle", "Shower", "Downpour",
        "Blaze", "Ember", "Flame", "Spark", "Flare", "Ignite",
    ],
    "abstract": [
        "Spirit", "Shadow", "Phantom", "Wraith", "Specter", "Ghost",
        "Dream", "Vision", "Mirage", "Illusion", "Fantasy", "Wonder",
        "Chaos", "Havoc", "Mayhem", "Fury", "Wrath",
        "Glory", "Honor", "Valor", "Courage", "Wisdom", "Fortune",
        "Legacy", "Destiny", "Karma", "Enigma", "Riddle", "Paradox",
        "Silence", "Whisper", "Echo", "Harmony", "Rhythm", "Melody",
        "Essence", "Aether", "Nimbus", "Zenith", "Apex", "Pinnacle",
    ],
}

ALL_CATEGORIES = tuple(WORD_LISTS.keys())


def _get_words(category: str | None = None) -> list[str]:
    if category and category in WORD_LISTS:
        return WORD_LISTS[category]
    words: list[str] = []
    for cat_words in WORD_LISTS.values():
        words.extend(cat_words)
    return list(set(words))


def find_category(word: str) -> str:
    for cat, words in WORD_LISTS.items():
        if word in words or word.capitalize() in words:
            return cat
    return "custom"


def generate_nicknames(
    count: int = 20,
    category: str | None = None,
    min_length: int = 5,
    max_length: int = 20,
) -> list[str]:
    words = _get_words(category)
    words = [w for w in words if min_length <= len(w) <= max_length]
    if not words:
        return []

    nicknames: list[str] = []
    used: set[str] = set()

    shuffled = words.copy()
    random.shuffle(shuffled)
    for w in shuffled:
        if len(nicknames) >= count:
            break
        if w not in used:
            nicknames.append(w)
            used.add(w)

    if len(nicknames) >= count:
        return nicknames[:count]

    for w in words:
        if len(nicknames) >= count:
            break
        for v in (w.lower(), w.upper()):
            if v not in used and min_length <= len(v) <= max_length:
                nicknames.append(v)
                used.add(v)
                if len(nicknames) >= count:
                    break

    return nicknames[:count]
