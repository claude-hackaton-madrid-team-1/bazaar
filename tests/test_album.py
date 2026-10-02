from bazaar_agent.album import album_view
from bazaar_agent.render import album_table
from tests.test_render import text

CATALOG = {
    "sets": [
        {
            "id": "LAV",
            "cards": [
                {"id": "LAV-01", "name": "La Corrala", "rarity": "common", "book": 10, "page": True},
                {"id": "LAV-03", "name": "X", "rarity": "common", "book": 10, "page": True},
                {"id": "LAV-09", "name": "Y", "rarity": "rare", "book": 70, "page": True},
                {"id": "LAV-12", "name": "Z", "rarity": "legendary", "book": 450, "page": False},
            ],
        },
        {"id": "RET", "cards": [{"id": "RET-01", "name": "R", "rarity": "common", "book": 10, "page": True}]},
    ]
}
ME = {
    "affinity": {"LAV": 1.6, "RET": 0.7},
    "album": {"pages": [{"set": "LAV", "name": "Lavapiés", "have": 1, "of": 10, "complete": False}]},
    "assets": [
        {"kind": "card", "ref": "LAV-01"},
        {"kind": "card", "ref": "LAV-01"},
        {"kind": "pack", "ref": "sobre_barrio"},
    ],
}


def test_album_lists_missing_page_cards_with_their_value_to_us_and_duplicates():
    [lav] = album_view(ME, CATALOG)  # RET is not in the album yet: not released
    assert [(m.ref, m.value_to_us) for m in lav.missing] == [("LAV-03", 16.0), ("LAV-09", 112.0)]
    assert (lav.affinity, lav.have, lav.duplicates) == (1.6, 1, ("LAV-01",))
    assert "LAV-09 R 112" in text(album_table([lav]))
