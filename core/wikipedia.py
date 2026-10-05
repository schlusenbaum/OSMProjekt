import json
import urllib.parse
import urllib.request


USER_AGENT = "OSMProjekt/1.0 (QMapShack route generator)"


def _get_json(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def get_wikipedia_info(wikidata_id: str) -> dict | None:
    """
    Holt die deutsche Wikipedia-Verknüpfung und den Kurztext
    über eine Wikidata-ID.

    Rückgabe:
        {
            "title": "...",
            "url": "...",
            "description": "...",
            "extract": "..."
        }

    None wird zurückgegeben, wenn keine verwertbaren Daten
    gefunden werden oder der externe Abruf fehlschlägt.
    """

    if not wikidata_id:
        return None

    try:
        wikidata_url = (
            "https://www.wikidata.org/wiki/Special:EntityData/"
            f"{urllib.parse.quote(wikidata_id)}.json"
        )

        data = _get_json(wikidata_url)
        entity = data["entities"][wikidata_id]

        sitelink = entity.get("sitelinks", {}).get("dewiki")

        if not sitelink:
            return None

        title = sitelink.get("title")
        if not title:
            return None

        wikipedia_url = (
            "https://de.wikipedia.org/wiki/"
            + urllib.parse.quote(title.replace(" ", "_"))
        )

        summary_url = (
            "https://de.wikipedia.org/api/rest_v1/page/summary/"
            + urllib.parse.quote(title.replace(" ", "_"))
        )

        summary = _get_json(summary_url)

        return {
            "title": title,
            "url": wikipedia_url,
            "description": summary.get("description"),
            "extract": summary.get("extract"),
        }

    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, ValueError):
        return None
