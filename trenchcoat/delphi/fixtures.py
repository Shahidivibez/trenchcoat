"""Fixture markets for mock mode — real wave-1 competition markets as published
on competition.delphi.fyi/markets.json (fetched 2026-11-27). Used only when the
live feed is unreachable; live/dry-run modes always prefer the real feed."""

FIXTURE_MARKETS = [
    {
        "address": "0x94dbba7c9b44b788a701602ab6d919ff7b7fc38a",
        "question": "Will turnout in Switzerland's federal vote of Nov 29, 2026 be at least 50.0%, per the Federal Chancellery?",
        "delphiUrl": "https://delphi.fyi/market/79ad7f1c-eb5c-48ed-b1cc-203f95d062f5",
        "category": "votes_government", "categoryLabel": "Votes and government",
        "marketTier": "A", "wave": 1, "status": "open",
        "odds": {"yes": 0.516056, "no": 0.483944},
        "closesAt": "2026-11-29T06:00:00.000Z",
        "evidenceCutoffAt": "2026-11-28T06:00:00.000Z",
        "pots": {"judgement": "20000000", "settlement": "30000000"},
        "resolutionCriteria": (
            "Yes, ≥50.0%: Wins if the Federal Chancellery's official turnout figure for "
            "Switzerland's federal vote of November 29, 2026 is 50.0% or higher. "
            "No, <50.0%: Wins if the official figure is below 50.0%. Turnout is the percentage "
            "of registered voters who cast a valid or blank ballot, as reported in the Federal "
            "Chancellery's official results. If only a preliminary figure is published within "
            "the window, that figure is used."
        ),
        "dataSources": ["https://www.bk.admin.ch", "https://opendata.swiss"],
        "settled": None,
    },
    {
        "address": "0x191257a08df3a827a9a02fa54926ed9507b4497a",
        "question": "Will Swiss voters approve the 19 Dec 2025 amendment to the War Materiel Act in the federal vote of Nov 29, 2026?",
        "delphiUrl": "https://delphi.fyi/market/4bff12b0-aa95-4bc2-9fbe-53fcaa307b60",
        "category": "votes_government", "categoryLabel": "Votes and government",
        "marketTier": "A", "wave": 1, "status": "open",
        "odds": {"yes": 0.54932, "no": 0.45068},
        "closesAt": "2026-11-29T06:00:00.000Z",
        "evidenceCutoffAt": "2026-11-28T06:00:00.000Z",
        "pots": {"judgement": "20000000", "settlement": "30000000"},
        "resolutionCriteria": (
            "Yes: Wins if the amendment of 19 December 2025 to the Federal Act on War Material "
            "(LFMG) receives a majority of valid votes cast in the Swiss federal popular vote "
            "held on 29 November 2026. No: Wins if it does not receive a majority of valid votes. "
            "The result is determined by the share of valid popular votes (not a double majority)."
        ),
        "dataSources": [
            "https://www.admin.ch/gov/en/start/documentation/votes.html",
            "https://www.seco.admin.ch/en/referendum-wma",
            "https://opendata.swiss", "https://www.srf.ch/news", "https://www.swissinfo.ch",
        ],
        "settled": None,
    },
    {
        "address": "0x55b0fabfcdf6125415854cc193ecd8cdc75bc382",
        "question": "Will Vox win ≥60 of 350 Congress seats in Spain's Nov 29, 2026 general election, per Interior Ministry provisional results?",
        "delphiUrl": "https://delphi.fyi/market/e11f656b-3ba0-4e84-aef4-8611397c6434",
        "category": "local_language", "categoryLabel": "Local-language sources",
        "marketTier": "A", "wave": 1, "status": "open",
        "odds": {"yes": 0.482591, "no": 0.517409},
        "closesAt": "2026-11-29T06:00:00.000Z",
        "evidenceCutoffAt": "2026-11-28T06:00:00.000Z",
        "pots": {"judgement": "20000000", "settlement": "30000000"},
        "resolutionCriteria": (
            "Yes, ≥60 seats: Wins if Vox is credited with 60 or more seats in the Congress of "
            "Deputies in the Spanish Interior Ministry's provisional results for the November 29, "
            "2026 general election. No, <60 seats: Wins if Vox is credited with fewer than 60. "
            "The seat count used is the Vox total in the escrutinio provisional, not the later "
            "escrutinio general published by the Central Electoral Board."
        ),
        "dataSources": ["https://infoelectoral.interior.gob.es/en/inicio",
                        "https://www.reuters.com", "https://apnews.com"],
        "settled": None,
    },
    {
        "address": "0x0d79f61a3cf6c6a83501fcb6abd22eddb4ab6a58",
        "question": "Will Destatis's preliminary German CPI (VPI) annual rate for Nov 2026, published 2026-11-30, be 2.8% or higher?",
        "delphiUrl": "https://delphi.fyi/market/821c90c8-5740-4a50-ac6b-03a1b462aa76",
        "category": "official_statistics", "categoryLabel": "Official statistics",
        "marketTier": "A", "wave": 1, "status": "open",
        "odds": {"yes": 0.568335, "no": 0.431665},
        "closesAt": "2026-11-30T08:00:00.000Z",
        "evidenceCutoffAt": "2026-11-29T08:00:00.000Z",
        "pots": {"judgement": "20000000", "settlement": "30000000"},
        "resolutionCriteria": (
            "Yes, 2.8% or higher: Wins if the Destatis preliminary Consumer Price Index (VPI) "
            "annual rate for November 2026, published in the press release scheduled for "
            "2026-11-30, is 2.8% or higher. No, below 2.8%: Wins if it is below 2.8%. If a "
            "corrected preliminary figure is published within the settlement window, the most "
            "recent figure takes precedence."
        ),
        "dataSources": ["https://www.destatis.de/EN/Themes/Economy/Prices/Consumer-Price-Index/_node.html"],
        "settled": None,
    },
    {
        "address": "0xecace7b63b2e044dd339e7512cf4360713a8e156",
        "question": "Will Swiss FSO report Nov 2026 CPI YoY inflation ≥1.6% in its Dec 2 release?",
        "delphiUrl": "https://delphi.fyi/market/18ad479a-3d92-43e0-8951-6f61085d6fb8",
        "category": "official_statistics", "categoryLabel": "Official statistics",
        "marketTier": "A", "wave": 1, "status": "open",
        "odds": {"yes": 0.477553, "no": 0.522447},
        "closesAt": "2026-12-02T07:00:00.000Z",
        "evidenceCutoffAt": "2026-12-01T07:00:00.000Z",
        "pots": {"judgement": "20000000", "settlement": "30000000"},
        "resolutionCriteria": (
            "Yes: Wins if the Swiss Federal Statistical Office (FSO) December 2, 2026 press "
            "release reports a November 2026 CPI year-on-year inflation rate of 1.6% or higher. "
            "No: Wins if the reported rate is below 1.6%."
        ),
        "dataSources": ["https://www.bfs.admin.ch", "https://opendata.swiss"],
        "settled": None,
    },
]
