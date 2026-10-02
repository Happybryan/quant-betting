"""Every recommendation records all four versions, stamped BEFORE the result is known."""
MODEL = "PP_PROP_v0.2"        # leg fair P = market consensus (Pinnacle no-vig + Kalshi mid); conservative = worst source
DATA = "DATA_v1.3"            # cloud: Pinnacle ML for every sport (sport-level jobs) + US props; laptop: Kalshi for every registry-matched series; health v2
VERIFY = "VERIFY_v2.1"      # + SGP_CORR_v1 same-game joint (2 legs); was v2.0        # gate + data-quality grade + frozen pre-bet snapshot
EXEC = "EXEC_v1.0"            # one official slip; AWAITING USER CONFIRMATION until the screenshot matches
PROTOCOL = f"{VERIFY}/{EXEC}"
