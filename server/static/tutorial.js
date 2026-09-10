/* Sea of Colours — teaching-mode film modal (v1.32).
 *
 * Loads AFTER app.js and talks to it through exactly two seams: the
 * `soc:tutorial-state` DOM event app.js fires when the turn changes, and
 * `window.__SOC_TUTORIAL__`. Nothing in app.js imports this file, so a
 * game runs identically without it — a teaching feature must not be able
 * to break a real season.
 *
 * WHAT THIS IS NOT: a wizard. It never greys the board, never takes the
 * pointer, never advances the game and never blocks a control. It opens
 * on the turn's reel, and the player closes it and plays. "Do not show
 * these again" is one click away and sticks.
 *
 * FILMS ARE OPTIONAL AT RUNTIME. Each chapter renders its prose whether
 * or not the .webm behind it exists yet, and a missing film degrades to a
 * labelled placeholder instead of a broken <video>. That is deliberate:
 * films are build output (backstage/films/make_tutorial_films.py), the reels are
 * source, and the two are allowed to be out of step during development.
 */
(function () {
  "use strict";

  var FILM_BASE = "/static/films/";
  var SEEN_KEY = "soc.tutorial.seen.v1";
  //: "Do not show these again" — nothing to do with SOUND, despite the
  //: key's name. Kept as `muted` in storage because the film harness and
  //: the shoot script both set it to keep the modal out of shot, and
  //: renaming it would silently un-suppress the tutorial for everyone
  //: who has already ticked the box. The code says `suppressed` (v1.47),
  //: because since narration arrived this file has two "mute"s and only
  //: one of them is about audio.
  var SUPPRESS_KEY = "soc.tutorial.muted.v1";
  //: Narration on/off. Off by default and deliberately so: a film that
  //: starts talking unprompted in a room of forty people is a worse
  //: first impression than a silent one, and most browsers would refuse
  //: to autoplay it audibly anyway.
  var NARRATE_KEY = "soc.tutorial.narrate.v1";

  /* ── The reels ──────────────────────────────────────────────────────
   *
   * Keyed `<preset>:<phase>:<day>` with a `*` wildcard on day, resolved
   * most-specific-first. Declarative on purpose: the turn-by-turn build
   * up the player asked for is a content decision, and it should be
   * editable here without touching the player, the resolver, or app.js.
   *
   * MIND THE DAY NUMBERS. A session opens on day 1 PLANNING, and the
   * night rolls the calendar before the orbit phase — so the FIRST orbit
   * a player ever sees is `orbit:2`, not `orbit:1`. There is no
   * `basic:orbit:1`, and a reel written under that key would simply
   * never open.
   *
   * `film` names a file in /static/films/. `body` is what the chapter
   * says in text — it is NOT a transcript of the film, it is the same
   * lesson written down, because a silent film that is missing, still
   * loading, or simply not the way someone prefers to learn has to leave
   * the player knowing the same thing.
   */
  var REELS = {
    "basic:planning:1": {
      title: "Night one — you cannot see anything yet",
      chapters: [
        {
          film: "01_basic_probe.webm",
          heading: "Probes cut holes in the fog",
          body:
            "The board starts blacked out. You have no idea where the RED is, "
            + "and you cannot land a harvester on ground you cannot see.\n\n"
            + "Probes are how you look. Click LAUNCH PROBE, then click the map: "
            + "the outlined area that follows your pointer is exactly what that "
            + "probe will reveal, so you are aiming a hole in the fog before you "
            + "spend it.\n\n"
            + "You can also right-click any square and pick 'launch probe here' "
            + "— same order, one click less.\n\n"
            + "You start with two probes. Use both. An unspent probe reveals "
            + "nothing.",
          todo: "Launch both probes, on two separate patches of fog.",
        },
        {
          film: "02_basic_praxis.webm",
          heading: "PRAXIS carries out the plan",
          body:
            "Nothing you click happens immediately. Orders stack up in the "
            + "queue as a PLAN, and you can hover any row to see what it will "
            + "do, reorder it, or throw it away.\n\n"
            + "When the plan is the one you want, hit PRAXIS. That locks it in "
            + "and the night resolves.\n\n"
            + "Remember your opponent is writing their own plan at the same "
            + "time, and you will not see it until the night plays out. Nobody "
            + "is reacting to anybody — you are both committing blind.",
          todo: "Read the queue back, then hit PRAXIS to run the night.",
        },
      ],
    },

    "basic:orbit:2": {
      title: "Orbit — spending the morning's credits",
      chapters: [
        {
          film: "03_basic_orbit_probes.webm",
          heading: "1000 credits a turn, and probes are what you can afford",
          body:
            "Between nights you are in orbit, and you get a fresh 1000 credits "
            + "each turn.\n\n"
            + "A harvester costs far more than one turn's income, so right now "
            + "the affordable buy is a couple more probes. That is the correct "
            + "opening: vision first, because everything else you might buy "
            + "needs somewhere to go.\n\n"
            + "Orbit works like the night does — queue the buys, then commit "
            + "the phase.",
          todo: "Buy two more probes, then commit the orbit phase.",
        },
      ],
    },

    "basic:planning:2": {
      title: "Night two — harvesters, and how to lose one",
      chapters: [
        {
          film: "04_basic_drop.webm",
          heading: "RED in the vault is the whole game — and not all RED is "
            + "worth the same",
          body:
            "Score comes from RED you actually bank. Seeing it does nothing; "
            + "harvesting it is the point.\n\n"
            + "But every RED square has a tier, and the tier is a multiplier "
            + "on its purity. Hover any square and the card does the sum for "
            + "you — purity, tier, and the score you would actually bank.\n\n"
            + "TRACE (purity 1–50) pays ×0.75. A whole hour of your night and "
            + "a slot of a six-slot hold, for about twenty points — usually "
            + "not worth the walk. VEIN (51–150) pays ×1.0, and is what most "
            + "seams are made of: unremarkable one square at a time, "
            + "significant in a run. MASS (151–254) pays ×1.5 and is the "
            + "workhorse — two good mass squares come to most of a jackpot. "
            + "PURE (255) pays ×3.0: 765 in one square, in one hour, which is "
            + "enough to change the pace of a game.\n\n"
            + "DROP puts a harvester on the surface, and only on ground you "
            + "can see RIGHT NOW — which is why last night was probes. Then "
            + "walk it: entering a RED square harvests it automatically, so a "
            + "chain of steps through a seam fills the hold as it goes.\n\n"
            + "Then watch the last shot. Every RED square you harvest turns "
            + "GREEN, and green banks at MINUS one hundred. You cannot decline "
            + "it — entering a coloured square harvests it, whoever you are. "
            + "The trail you leave behind is a minefield, for them and for "
            + "you.",
          todo: "Hover a few RED squares and compare the scores. Then land on "
            + "the richest seam you can see, walk it, and LIFT.",
        },
        {
          film: "05_basic_stranded.webm",
          heading: "A harvester you do not lift is a harvester you lose",
          body:
            "This is the one that catches everybody, so the film does not "
            + "describe it — it goes through with it and lets you watch.\n\n"
            + "End your queue with LIFT. A harvester still standing on the "
            + "surface when the night ends is destroyed by the dawn wave, and "
            + "everything in its hold goes with it. The film stays on the "
            + "square afterwards: what is left is a permanent wreck marker, "
            + "and hovering it tells you which day you lost it.\n\n"
            + "The game does warn you first. With no LIFT queued the fleet row "
            + "stickers the harvester STRANDED, and TRANSMIT refuses the first "
            + "click and asks again. In the film that warning is ignored on "
            + "purpose.\n\n"
            + "Drop, walk, LIFT. Every time.\n\n"
            + "And spend your probes again this turn. Vision expires, the ground "
            + "you lit last night goes dark, and a probe sitting in stock is "
            + "worth nothing.",
          todo: "End the queue with LIFT — and spend both probes again.",
        },
      ],
    },

    "basic:orbit:3": {
      title: "Orbit — where the score actually comes from",
      chapters: [
        {
          film: "06_basic_score.webm",
          heading: "Ground, hold, station, catapult, score",
          body:
            "If you harvested last night and the scoreboard did not move, "
            + "nothing went wrong. The chain is longer than one night, and "
            + "this is the only part of the game you cannot work out by "
            + "playing it.\n\n"
            + "RED in the ground is worth nothing. Walking a square puts it in "
            + "the harvester's hold. LIFT carries the hold up to your station. "
            + "Committing the orbit phase loads the catapult — you will see a "
            + "pending +N appear under your score, which is not score yet. The "
            + "catapult throws on the NEXT night's dusk, and only then does "
            + "the number on the board move.\n\n"
            + "The film follows one load all the way through without cutting, "
            + "so you can see exactly where the delay is.",
          todo: "Commit the orbit to load the catapult. It throws tomorrow night.",
        },
        {
          film: "07_basic_buy_harvester.webm",
          heading: "Two harvesters, two seams",
          body:
            "By now you have banked something and you have credits stacked from "
            + "a turn you spent lightly. A harvester is the big purchase, and "
            + "the reason to make it is simple: one harvester can only work one "
            + "seam a night.\n\n"
            + "Watch the projected wallet as you queue — it shows what you will "
            + "have left, so you can see whether the buy leaves you able to "
            + "probe as well.",
          todo: "Buy a second harvester, if the projected wallet still leaves "
            + "you a probe.",
        },
      ],
    },

    "basic:planning:3": {
      title: "Last night — traffic, and stale eyes",
      chapters: [
        {
          film: "08_basic_crash.webm",
          heading: "Two harvesters cannot share a square",
          body:
            "It happens in two shapes, and the film shows both in one real "
            + "night against a live opponent — close in on the board, because "
            + "a collision is over in about a second.\n\n"
            + "Both Houses picked the same landing square. Neither lands: both "
            + "harvesters are damaged and both stay in orbit.\n\n"
            + "Then a harvester walks into a square the other House is already "
            + "standing on. Neither moves, and both are damaged again.\n\n"
            + "A damaged harvester cannot step or harvest, and it cannot be "
            + "deployed again until you pay to repair it in orbit. On the last "
            + "night that is effectively a write-off.\n\n"
            + "You cannot see their plan, but you can see where they have been "
            + "working. Read the trails before you route through them.",
          todo: "Route both harvesters clear of the trails they left last night.",
        },
        {
          film: "09_basic_supersede.webm",
          heading: "A newer probe wins the square",
          body:
            "Probes do not stack usefully. Where two of your probe fields "
            + "overlap, the newer reading supersedes the older one for that "
            + "square — so a probe dropped on ground you already lit buys you "
            + "much less than one dropped on ground you have never seen.\n\n"
            + "Spread them. It is the last night: light the ground you intend "
            + "to walk, and walk everything you light.",
          todo: "Probe fog you have never lit, and lift everything before dawn.",
        },
      ],
    },

    /* ── ADVANCED ────────────────────────────────────────────────────
     *
     * Basic teaches the machine: probes, drops, walks, lifts, and the
     * ways a night can go wrong on its own. Advanced teaches the other
     * House — what you can read about them without seeing them, and
     * what you can do to them without touching them.
     *
     * FOUR nights and three orbits since v1.36 (Basic is still three).
     * The arc is the economy: the blue you land on in night one is
     * literally what pays for the weapons in orbit, because 250 blue a
     * season buys exactly one EMP and nothing else.
     *
     * One weapon per orbit, fired the night after — buy EMP / buy SNAP
     * / buy chaff, against nights that give each one something to do.
     * Three weapons will not fit in three nights, and the fourth night
     * bought two other things worth having: night two was the heaviest
     * turn in either tutorial at four chapters and is now two, and
     * `adv_chaff` finally plays on the night it was SHOT on. That film
     * needs a rival to land, work and be caught mid-lift, which takes a
     * night four; on the old cap the reel ran on night three and showed
     * the player a sequence their own game could no longer produce.
     *
     * Chapter load, and please keep it flat if you edit this:
     *   n1 1 · o2 1 · n2 2 · o3 1 · n3 3 · o4 2 · n4 2   (12 total)
     */

    "advanced:planning:1": {
      title: "Night one — the map is dark, and it is still telling you things",
      chapters: [
        {
          film: "10_adv_hotdrop.webm",
          heading: "Blue signs, and landing on ground you cannot see",
          body:
            "Blue is radioactive. Every blue pocket smears a glow across "
            + "the fog around it, and that smear has been on your map since "
            + "the season opened — before you probed anything.\n\n"
            + "It is deliberately vague. It says a pocket is somewhere "
            + "under here; it does not say which square, and it never "
            + "fades, so a sign over ground somebody already stripped "
            + "looks exactly like a sign over a full pocket.\n\n"
            + "The play is the HOT DROP. Probe the brightest part on one "
            + "hour, and queue the landing for the next hour — into a "
            + "hole that has not been cut yet. You are committing to "
            + "ground you will not see until the night is already "
            + "running. That is not recklessness; under live-only drops "
            + "it is the only way to reach anything on turn one.\n\n"
            + "And blue is not score. Blue is the entire weapons budget.",
          todo: "Probe the brightest blue smear, then hot-drop into it "
            + "the hour after.",
        },
      ],
    },

    "advanced:orbit:2": {
      title: "Orbit — what blue is actually for",
      chapters: [
        {
          film: "11_adv_buy_emp.webm",
          heading: "Credits buy hulls. Weapons cost blue.",
          body:
            "Two currencies, and they do not convert. Credits arrive on "
            + "their own, 1000 a turn, and buy harvesters, probes and "
            + "repairs. Blue has to be dug out of the ground, and it is "
            + "the only thing weapons take.\n\n"
            + "You start with 250 blue. An EMP is 200 of it plus 250 "
            + "credits. So the season hands you exactly one weapon and "
            + "then stops — everything after that is blue you went and "
            + "harvested.\n\n"
            + "Buy the EMP now. You will fire it in two nights.",
          todo: "Buy one EMP, and a couple of probes with the credits.",
        },
      ],
    },

    "advanced:planning:2": {
      title: "Night two — the REDSIGN, and why jackpots cannot be kept quiet",
      chapters: [
        {
          film: "12_adv_redsign.webm",
          heading: "Find a pure seam and the whole board is told",
          body:
            "A handful of squares on the map are PURE — 255, the richest "
            + "the generator makes. The moment any probe or harvester "
            + "brings one into live vision for the first time, the board "
            + "mints a REDSIGN.\n\n"
            + "A redsign is public. Every House gets it, on the same hour, "
            + "and it never says who lit it.\n\n"
            + "But it is not a pin on the square. The red smear is centred "
            + "on a point knocked off the real seam and spreads well past "
            + "it — it says a pure seam is somewhere around HERE, not "
            + "which square it is.\n\n"
            + "And you are standing on it in live vision. You can see the "
            + "exact cell; they get a rough area. That gap is your whole "
            + "head start, and it lasts only as long as it takes them to "
            + "probe into it.",
          todo: "Probe toward a pure seam — and expect company the moment "
            + "you find one.",
        },
        {
          film: "13_adv_redsign_rival.webm",
          heading: "A redsign you did not light",
          body:
            "The same mechanic from the other side. You probe your own "
            + "ground, and a redsign comes up somewhere you have never "
            + "been, with nothing of yours near it.\n\n"
            + "That is the other House walking onto a pure seam. You learn "
            + "on the same hour they do — but you learn the approximate "
            + "area and nothing else. Not the square, not who found it, "
            + "not how much is in it, not whether they can reach it. Only "
            + "the finder knows exactly where it is.\n\n"
            + "The smear paints on FOG, so it burns off as you close in: "
            + "probe into it and the hint is replaced by real ground, and "
            + "you find the seam yourself or you find out it was never "
            + "worth the trip.\n\n"
            + "Pures are rationed — two or three on a board this size — "
            + "and each is worth several ordinary seams. A redsign is not "
            + "information you can sit on. It is a start gun.",
          todo: "Decide now: probe into their redsign to pin the square, "
            + "or bank the seam you already hold.",
        },
      ],
    },

    "advanced:orbit:3": {
      title: "Orbit — the cheap weapon, and the one that arrives first",
      chapters: [
        {
          film: "14_adv_buy_snap.webm",
          heading: "A hundred blue buys one square of one hour",
          body:
            "Weapons are priced 100, 200, 300 — a SNAP, an EMP, a chaff "
            + "flare — against a rack that holds 600. SNAP is the cheap "
            + "one, and the only one you can afford out of what the EMP "
            + "left you.\n\n"
            + "You get very little for it. One missile, one square, one "
            + "hour. No salvo, no wall, no eight-hour cloud.\n\n"
            + "What you buy instead is the front of the queue. Every "
            + "other weapon lands inside the hour along with everything "
            + "else; SNAP lands ahead of it, before the board takes its "
            + "note of who can see what. That single beat of head start "
            + "is the whole weapon, and tomorrow night is where it pays "
            + "for itself.\n\n"
            + "Check your log — the training range has credited you the "
            + "hundred, exactly one round.",
          todo: "Buy one SNAP, and put the credits into probes.",
        },
      ],
    },

    "advanced:planning:3": {
      title: "Night three — the jackpot fight, and the beat that decides it",
      chapters: [
        {
          film: "15_adv_smash_grab.webm",
          heading: "Smash and grab — take the pure you can see, and take "
            + "it badly",
          body:
            "You can see the square. The instinct is to work the seam "
            + "around it and lift with a full hold; the arithmetic says "
            + "otherwise.\n\n"
            + "A pure is 765 in ONE hour. The five ordinary squares behind "
            + "it are about a thousand between them, and they cost five "
            + "more hours plus the lift. The greedy line does bank more — "
            + "and it leaves the 765 sitting on the board for seven hours "
            + "with your harvester parked next to it.\n\n"
            + "So land ON the pure and lift on the next hour. Two hours, "
            + "one parcel, five slots of hold you never open. What you buy "
            + "is certainty: ore in the hold cannot be harvested, contested "
            + "or found by anybody.\n\n"
            + "Two things still beat it. Chaff on your lift hour leaves you "
            + "with no ride home and dawn takes the hull and the hold. And "
            + "a House with its own live vision on that square can drop "
            + "into it on the same hour — then nobody lands and both hulls "
            + "come home damaged.",
          todo: "If you can see a pure, price the greedy line honestly — "
            + "then take the jackpot and go.",
        },
        {
          film: "16_adv_blind_grab.webm",
          heading: "Blind and grab — attack a jackpot you cannot see",
          body:
            "The other case: the beacon is lit and you have never been "
            + "near it. You cannot land on a square you cannot see, and "
            + "probing in costs you the hour that tells them you are "
            + "coming.\n\n"
            + "So attack the situation instead of the square. Fry the "
            + "probe that lit the beacon — now nobody can land on that "
            + "seam, including them. Put your own eye on the edge of the "
            + "smear. And commit the harvester in the same queue, before "
            + "that eye has reported anything, to a landing and five steps "
            + "picked off the smear.\n\n"
            + "Aim the EMP wall UNDER the seam, not over it. Your own cloud "
            + "denies your own harvests too — there is no friendly-fire "
            + "switch — so the missiles have to catch their probe without "
            + "covering the ground you intend to walk.\n\n"
            + "You will probably miss the pure. What you get is a hold of "
            + "ordinary red, their eye put out, and — because your probe "
            + "walked past it — the exact square, for tomorrow. It is not "
            + "a jackpot play, it is a tempo play.",
          todo: "If the beacon is somebody else's, take their eye out first "
            + "and comb the smear while they are blind.",
        },
        {
          film: "17_adv_snap.webm",
          heading: "SNAP — the answer to the two grabs you just learned",
          body:
            "Both grabs have the same weak point, and it is not the "
            + "harvester. It is the eye.\n\n"
            + "Landing needs LIVE vision of the square, and the board "
            + "decides who has live vision once, at the top of each "
            + "hour. Everything else in the game happens under that "
            + "note — including the EMP. Fry a beacon with an EMP on the "
            + "same hour somebody is landing on it and the landing still "
            + "goes through, because the board had already written down "
            + "that the square was lit.\n\n"
            + "SNAP resolves ABOVE that line. It is the one thing on the "
            + "board that moves before the note is taken. Kill the probe "
            + "with a SNAP and the square was never lit — so the drop it "
            + "was lighting is refused TONIGHT, not tomorrow.\n\n"
            + "That is a smash-and-grab stopped dead for a hundred blue, "
            + "and it is the only way to do it. You are not out-shooting "
            + "them. You are arriving first.\n\n"
            + "It has a second use if you would rather gamble than deny. "
            + "The square stays hot for the rest of that hour, and any "
            + "harvester on it — standing there already, or walking or "
            + "landing into it afterwards — is damaged, which means it "
            + "harvests nothing that turn and costs 500 credits to put "
            + "right. Guess the square somebody is dropping onto and you "
            + "take their whole night rather than one hour of it.\n\n"
            + "Aim it like anything else: right-click the square. Friendly "
            + "fire is on, so do not SNAP your own beacon.",
          todo: "SNAP the probe lighting a pure you cannot hold — before "
            + "they land on it.",
        },
      ],
    },

    "advanced:orbit:4": {
      title: "Orbit — a second hull, and the dearest thing on the board",
      chapters: [
        {
          film: "18_adv_buy_chaff.webm",
          heading: "Chaff costs no credits, and more blue than you own",
          body:
            "A harvester first: two seams need two hulls, and by now you "
            + "know where two seams are.\n\n"
            + "Then look at chaff. It costs nothing in credits and 300 in "
            + "blue — more than a whole season's starting stipend, of "
            + "which you have spent 200 on the EMP and 100 on the SNAP. "
            + "There is no turn on which you could simply have bought "
            + "it.\n\n"
            + "So this one is on the house. Check your log: the training "
            + "range has just credited you 300 blue, exactly one flare, "
            + "and it will not do it again. In a real season that blue "
            + "comes out of the ground or it does not come at all — which "
            + "is the actual lesson, and the reason you are being shown "
            + "the gift rather than handed it quietly.",
          todo: "Buy a second harvester, then buy the chaff with the "
            + "granted blue.",
        },
        {
          film: "19_adv_arms_bar.webm",
          heading: "Everyone can see what you are carrying",
          body:
            "Watch the station as the flare is built. The blue does not "
            + "vanish — it turns cyan and flies to the bar on the far "
            + "side of the hull from your vault. That bar is your "
            + "ARSENAL: six pips, one per hundred blue of ordnance, up to "
            + "a hard ceiling of 600. One pip is already lit; that is "
            + "last turn's SNAP. The flare is worth three more.\n\n"
            + "Two things follow, and they are the point of this "
            + "chapter.\n\n"
            + "The ceiling is real. At 600 the buy buttons grey out and "
            + "say so. You cannot stockpile your way out of a bad "
            + "position; a weapon you are hoarding is blue you are not "
            + "spending, and at some point the rack simply refuses.\n\n"
            + "And the bar is PUBLIC. Not a grade, not a guess — the "
            + "exact figure, on every station, to every House, including "
            + "yours on theirs. Your vault is still a silhouette to them. "
            + "Your weapons are not. Hover any rival station and read "
            + "their arsenal off the card.\n\n"
            + "That cuts both ways and it is meant to. You will never be "
            + "ambushed by a weapon nobody could have known about — and "
            + "neither will they. The moment you arm, every seat on the "
            + "board is planning around it.\n\n"
            + "What it does NOT tell them is which weapons. At 100, 200 "
            + "and 300 the figure rarely has one answer: your 400 is a "
            + "SNAP and a chaff, and it is also two EMPs, and it is also "
            + "four SNAPs. They know exactly how much is pointed at them "
            + "and mostly not what it is — so read a rival's bar as a "
            + "budget, not an inventory.\n\n"
            + "It reads the other way too. Watch the bars through a "
            + "night: a pip goes out the hour a weapon is fired, not the "
            + "next morning. If a rival's arsenal empties at hour four "
            + "and you are still standing at hour five, whatever they "
            + "spent it on has already happened to somebody.",
          todo: "Hover the rival station and read its arsenal line.",
        },
      ],
    },

    "advanced:planning:4": {
      title: "Last night — taking the clock, and taking the ride home",
      chapters: [
        {
          film: "20_adv_emp.webm",
          heading: "EMP does not take the red. It takes the clock — if you "
            + "are willing to wait for it.",
          body:
            "One launch is a salvo of three missiles, each a radius-2 "
            + "diamond. Placed apart they are three puddles; overlapped "
            + "they are one wall.\n\n"
            + "A cloud destroys probes and stands for EIGHT hours. It does "
            + "not destroy harvesters — it stops them acting — so it is "
            + "not how you kill anything. What it does is take away sight. "
            + "Landing needs live vision of the square, so a House whose "
            + "eye you fried cannot land on that seam however well they "
            + "know it is there, and the beacon stays lit the whole time.\n\n"
            + "Which means you have bought eight hours in which a seam is "
            + "yours alone. The film shows the same night played twice, "
            + "because the trap here is impatience.\n\n"
            + "Walk in while the cloud is live and you do not merely lose "
            + "the harvest: a hull inside a live cloud cannot act at all. "
            + "It stands there, hour after hour, and lifts with an empty "
            + "hold. Land just outside instead, spend the hours on WAIT, "
            + "and step in as the cloud expires — same salvo, same "
            + "harvester, same squares, and this time the seam and their "
            + "jackpot come home with you.\n\n"
            + "They can read that timer too. The difference is being "
            + "parked next to the seam when it runs out.",
          todo: "Overlap the three missiles into one wall, land clear of "
            + "it, and WAIT out your own cloud before you walk in.",
        },
        {
          film: "21_adv_chaff.webm",
          heading: "Chaff denies three hours. Aimed at one, it kills.",
          body:
            "A flare jams every House for three hours — yours included, "
            + "which is why it occupies three slots of your own queue.\n\n"
            + "Start with what that is worth on an ordinary turn. Three "
            + "hours in which nobody lands, nobody walks and nobody lifts "
            + "is three hours in which a contested seam belongs to nobody. "
            + "Flare over a pure that neither House has grabbed yet and it "
            + "is simply not available — while you are already walking at "
            + "it, or setting up the blind grab. Denial is not a "
            + "consolation prize; most turns it is the whole point.\n\n"
            + "Then there is what the same three hours become when they "
            + "are aimed at ONE. The lifter is the only way off the "
            + "surface, a cancelled pickup burns its slot, and anything "
            + "still standing at Aurora is destroyed by the dawn wave along "
            + "with its whole hold.\n\n"
            + "So you do not jam their landing or their walk. You let them "
            + "do all of it, and jam the hour they reach for the ride "
            + "home. That means reading their queue from the outside: they "
            + "landed on that hour, they have walked this many squares, so "
            + "the lift is about now.\n\n"
            + "Denial buys you a seam. The lifter buys you the harvester "
            + "and everything in it.",
          todo: "Let them work. Flare on the hour you think their lift is "
            + "queued.",
        },
      ],
    },
  };

  /* Which reel applies right now. Most specific key first, so a
   * day-specific reel beats the catch-all without the caller ordering
   * anything. */
  function resolveReel(preset, phase, day) {
    if (!preset) return null;
    var ph = String(phase || "").toLowerCase();
    var keys = [
      preset + ":" + ph + ":" + day,
      preset + ":" + ph + ":*",
      preset + ":*:" + day,
    ];
    for (var i = 0; i < keys.length; i++) {
      if (REELS[keys[i]]) return { key: keys[i], reel: REELS[keys[i]] };
    }
    return null;
  }

  /* ── Persistence ────────────────────────────────────────────────────
   * Which reels have already been shown, so the modal auto-opens ONCE
   * per turn rather than on every four-second poll.
   *
   * v1.33 — scoped to the GAME, not just the reel. The first cut keyed
   * on the reel alone, reasoning that replaying the tutorial should not
   * re-nag. That was exactly backwards: the reel keys are
   * `basic:planning:1` and friends, identical in every Basic game, so
   * anyone who had played once got a tutorial with no tutorial in it
   * and no way to tell why. Starting a fresh teaching game is a request
   * to be taught. The mute box is the "stop showing me these" control,
   * and it still is.
   *
   * Old un-scoped entries are ignored rather than migrated: at worst
   * someone mid-game sees one reel a second time.
   *
   * localStorage failures are swallowed — a private window must still
   * be able to play, and there the auto-open simply repeats. */
  function seenSet() {
    try {
      var raw = window.localStorage.getItem(SEEN_KEY);
      var arr = raw ? JSON.parse(raw) : [];
      return Array.isArray(arr) ? arr : [];
    } catch (e) { return []; }
  }

  /* A reel is "seen" only within the game that showed it. Without a
   * game id (an older app.js, say) fall back to the bare reel key so
   * the modal still opens once per turn instead of on every poll. */
  function seenKey(reelKey) {
    return state.game ? state.game + "|" + reelKey : reelKey;
  }

  function markSeen(reelKey) {
    try {
      var arr = seenSet();
      var k = seenKey(reelKey);
      if (arr.indexOf(k) === -1) arr.push(k);
      // A browser that plays many tutorials should not grow this
      // forever; only the current game's entries can still matter.
      if (arr.length > 200) arr = arr.slice(-100);
      window.localStorage.setItem(SEEN_KEY, JSON.stringify(arr));
    } catch (e) { /* private window — auto-open just repeats, harmless */ }
  }

  function isSuppressed() {
    try { return window.localStorage.getItem(SUPPRESS_KEY) === "1"; }
    catch (e) { return false; }
  }

  function setSuppressed(on) {
    try { window.localStorage.setItem(SUPPRESS_KEY, on ? "1" : "0"); }
    catch (e) { /* ignore */ }
  }

  function isNarrating() {
    try { return window.localStorage.getItem(NARRATE_KEY) === "1"; }
    catch (e) { return false; }
  }

  function setNarrating(on) {
    try { window.localStorage.setItem(NARRATE_KEY, on ? "1" : "0"); }
    catch (e) { /* ignore */ }
  }

  /* ── DOM ────────────────────────────────────────────────────────────
   * Built once, lazily, and reused. Kept out of index.html because the
   * markup is meaningless without this file. */
  var el = null;
  var state = { reel: null, key: "", idx: 0, game: "", scoped: "" };

  function build() {
    if (el) return el;
    var root = document.createElement("div");
    root.className = "soc-tut";
    root.hidden = true;
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-modal", "false");
    root.setAttribute("aria-label", "Tutorial");
    root.innerHTML = [
      '<div class="soc-tut-scrim" data-tut-close></div>',
      '<div class="soc-tut-panel">',
      '  <div class="soc-tut-head">',
      '    <span class="soc-tut-title" data-tut-title></span>',
      '    <span class="soc-tut-step" data-tut-step></span>',
      '    <button type="button" class="soc-tut-x" data-tut-close',
      '            aria-label="Close tutorial">\u00D7</button>',
      '  </div>',
      '  <div class="soc-tut-stage" data-tut-stage></div>',
      '  <div class="soc-tut-body">',
      '    <h3 class="soc-tut-heading" data-tut-heading></h3>',
      '    <div class="soc-tut-prose" data-tut-prose></div>',
      '  </div>',
      // The prose explains the mechanic; this says what to actually do
      // about it. Deliberately a sibling of the body rather than the
      // last thing inside it: the body scrolls, and on a long card the
      // one line the player most needs was sitting below the fold.
      '  <div class="soc-tut-todo" data-tut-todo hidden>',
      '    <span class="soc-tut-todo-tag">do this turn</span>',
      '    <span class="soc-tut-todo-text" data-tut-todo-text></span>',
      '  </div>',
      '  <div class="soc-tut-foot">',
      '    <label class="soc-tut-mute">',
      '      <input type="checkbox" data-tut-mute> don\u2019t show these again',
      '    </label>',
      // Hidden until a film turns out to HAVE a voice track. Most do
      // not yet, and a narration button that does nothing on nineteen
      // chapters out of twenty-one teaches people to ignore it.
      '    <button type="button" class="cli-btn soc-tut-narrate"',
      '            data-tut-narrate hidden aria-pressed="false"></button>',
      '    <span class="soc-tut-nav">',
      '      <button type="button" class="cli-btn" data-tut-prev>&larr; back</button>',
      '      <button type="button" class="cli-btn" data-tut-next>next &rarr;</button>',
      '      <button type="button" class="cli-btn soc-tut-go" data-tut-close>start playing</button>',
      '    </span>',
      '  </div>',
      '</div>',
    ].join("\n");
    document.body.appendChild(root);

    root.addEventListener("click", function (ev) {
      var t = ev.target;
      if (!(t instanceof Element)) return;
      if (t.closest("[data-tut-close]")) { close(); return; }
      if (t.closest("[data-tut-prev]")) { step(-1); return; }
      if (t.closest("[data-tut-next]")) { step(1); return; }
      if (t.closest("[data-tut-narrate]")) {
        // This click is also the user gesture that earns the right to
        // play audio at all, so turning narration ON here always works
        // — whereas honouring a remembered preference on load may not.
        setNarrating(!isNarrating());
        applyNarration(root.querySelector("video"), true);
        return;
      }
    });
    var mute = root.querySelector("[data-tut-mute]");
    if (mute) {
      mute.addEventListener("change", function () {
        setSuppressed(Boolean(mute.checked));
      });
    }
    document.addEventListener("keydown", function (ev) {
      if (root.hidden) return;
      if (ev.key === "Escape") { close(); }
      else if (ev.key === "ArrowRight") { step(1); }
      else if (ev.key === "ArrowLeft") { step(-1); }
    });
    el = root;
    return el;
  }

  /* ── Narration (v1.47) ──────────────────────────────────────────────
   *
   * Films are shot silent and some are then narrated (see
   * backstage/films/voice/). Three things the player does have to move
   * together, and each has a reason that is not obvious:
   *
   *   muted  — every film STARTS muted, always. Browsers refuse audible
   *            autoplay without a user gesture, and a video that is
   *            refused does not play at all: honouring a remembered
   *            "narration on" eagerly would trade a silent film for a
   *            frozen one. So it starts muted and unmutes once it can.
   *   loop   — off while narrating. A voice restarting mid-sentence
   *            every time a 30-second clip comes round is worse than
   *            silence; looping is only right for a silent clip someone
   *            watches three times to catch the detail.
   *   replay — turning narration on restarts the film. The alternative
   *            is joining a sentence halfway, which is how you get a
   *            player who thinks the narration is broken.
   */

  /* Does this file actually carry a voice track?
   *
   * Asked rather than declared because the alternative is a list of
   * narrated films in this file, and that list would be wrong the first
   * time someone narrates one and forgets to come back here. Every
   * engine spells the answer differently and Chromium only answers once
   * some audio has been decoded, which is why the caller polls. */
  function filmHasAudio(v) {
    if (!v) return false;
    if (typeof v.mozHasAudio === "boolean") return v.mozHasAudio;
    if (v.audioTracks && typeof v.audioTracks.length === "number") {
      return v.audioTracks.length > 0;
    }
    if (typeof v.webkitAudioDecodedByteCount === "number") {
      return v.webkitAudioDecodedByteCount > 0;
    }
    return false;
  }

  function paintNarrateBtn(has, on, blocked) {
    var root = el;
    if (!root) return;
    var b = root.querySelector("[data-tut-narrate]");
    if (!b) return;
    b.hidden = !has;
    b.textContent = blocked
      ? "click to hear narration"
      : (on ? "narration: on" : "narration: off");
    b.setAttribute("aria-pressed", on ? "true" : "false");
    b.classList.toggle("is-on", Boolean(on) && !blocked);
  }

  function applyNarration(v, restart) {
    if (!v || !v.parentNode) return;
    var has = filmHasAudio(v);
    var want = has && isNarrating();
    v.loop = !want;
    if (!want) {
      v.muted = true;
      paintNarrateBtn(has, false, false);
      return;
    }
    v.muted = false;
    if (restart) { try { v.currentTime = 0; } catch (e) { /* ignore */ } }
    var p = v.play();
    paintNarrateBtn(true, true, false);
    // A rejected play() is the autoplay policy, not a broken file. Fall
    // back to the silent film and say what would fix it, rather than
    // leaving a stopped video and no explanation.
    if (p && typeof p.catch === "function") {
      p.catch(function () {
        v.muted = true;
        v.loop = true;
        try { v.play(); } catch (e) { /* ignore */ }
        paintNarrateBtn(true, false, true);
      });
    }
  }

  /* A film, or an honest placeholder. `preload="auto"` and `loop` are
   * both wanted: these are short clips and a first-timer will watch one
   * three times. */
  function stageFor(chapter) {
    var stage = document.createElement("div");
    if (!chapter.film) {
      stage.className = "soc-tut-stage-empty";
      stage.textContent = "";
      return stage;
    }
    var v = document.createElement("video");
    v.className = "soc-tut-video";
    v.src = FILM_BASE + chapter.film;
    v.autoplay = true;
    v.loop = true;
    v.muted = false;
    v.playsInline = true;
    v.preload = "auto";
    v.setAttribute("aria-label", chapter.heading || "tutorial film");
    v.addEventListener("error", function () {
      var miss = document.createElement("div");
      miss.className = "soc-tut-stage-empty";
      miss.textContent =
        "film not generated yet \u2014 the text below says the same thing";
      if (v.parentNode) v.parentNode.replaceChild(miss, v);
    });
    // Chromium reports an audio track only once it has decoded some, so
    // one check on loadeddata finds nothing. Poll briefly, then give up:
    // a film with no voice must not leave a spinner or a dead button.
    var tries = 0;
    var poll = window.setInterval(function () {
      tries += 1;
      if (!v.parentNode) { window.clearInterval(poll); return; }
      if (filmHasAudio(v)) {
        window.clearInterval(poll);
        applyNarration(v, isNarrating());
      } else if (tries > 12) {
        window.clearInterval(poll);
        paintNarrateBtn(false, false, false);
      }
    }, 250);
    return v;
  }

  function render() {
    var root = build();
    var reel = state.reel;
    if (!reel) return;
    var n = reel.chapters.length;
    var i = Math.max(0, Math.min(n - 1, state.idx));
    var ch = reel.chapters[i];

    root.querySelector("[data-tut-title]").textContent = reel.title;
    root.querySelector("[data-tut-step]").textContent =
      n > 1 ? (i + 1) + " / " + n : "";
    root.querySelector("[data-tut-heading]").textContent = ch.heading || "";

    var prose = root.querySelector("[data-tut-prose]");
    prose.textContent = "";
    String(ch.body || "").split("\n\n").forEach(function (para) {
      var p = document.createElement("p");
      p.textContent = para;
      prose.appendChild(p);
    });

    var todo = root.querySelector("[data-tut-todo]");
    var todoText = root.querySelector("[data-tut-todo-text]");
    if (todo && todoText) {
      todoText.textContent = String(ch.todo || "");
      todo.hidden = !ch.todo;
    }

    var stage = root.querySelector("[data-tut-stage]");
    stage.textContent = "";
    // Hide the control before the new film is in: whether it has a
    // voice is a fact about THIS chapter, and carrying the last one's
    // answer over would offer narration on a silent film.
    paintNarrateBtn(false, false, false);
    stage.appendChild(stageFor(ch));

    var prev = root.querySelector("[data-tut-prev]");
    var next = root.querySelector("[data-tut-next]");
    prev.hidden = i === 0;
    next.hidden = i >= n - 1;
    var mute = root.querySelector("[data-tut-mute]");
    if (mute) mute.checked = isSuppressed();
  }

  function step(d) {
    if (!state.reel) return;
    var n = state.reel.chapters.length;
    var next = state.idx + d;
    if (next < 0 || next >= n) return;
    state.idx = next;
    render();
  }

  function open(idx) {
    if (!state.reel) return;
    // Opening now settles any deferred open — whether this came from the
    // timer, the TUTORIAL button or the film harness driving us directly.
    cancelPendingOpen();
    state.idx = Number.isFinite(idx) ? idx : 0;
    var root = build();
    root.hidden = false;
    render();
    markSeen(state.key);
  }

  function close() {
    if (!el) return;
    el.hidden = true;
    // Stop the clip rather than leaving it looping behind the modal.
    var v = el.querySelector("video");
    if (v) { try { v.pause(); } catch (e) { /* ignore */ } }
  }

  /* ── The in-game button ─────────────────────────────────────────────
   * Present only in a teaching game, and only when the current turn
   * actually has a reel — a TUTORIAL button that opens nothing is worse
   * than no button. */
  var btn = null;

  function ensureButton() {
    if (btn) return btn;
    btn = document.createElement("button");
    btn.type = "button";
    btn.id = "soc-tutorial-btn";
    btn.className = "cli-btn soc-tut-btn";
    btn.textContent = "[ TUTORIAL ]";
    btn.title = "Replay this turn's tutorial films";
    btn.addEventListener("click", function () { open(0); });
    var host =
      document.querySelector("[data-tutorial-slot]")
      || document.querySelector(".topbar-right")
      || document.querySelector("header")
      || document.body;
    host.appendChild(btn);
    return btn;
  }

  /** v1.35 — a breath after the board settles before the modal covers
   *  it, so the last frame of whatever just resolved actually registers
   *  as having finished rather than being guillotined by a popup. */
  var OPEN_AFTER_BEAT_MS = 1000;
  var pendingOpen = false;
  var openTimer = null;

  function cancelPendingOpen() {
    pendingOpen = false;
    if (openTimer) { clearTimeout(openTimer); openTimer = null; }
  }

  function openAfterBeat() {
    if (openTimer) clearTimeout(openTimer);
    openTimer = setTimeout(function () {
      openTimer = null;
      open(0);
    }, OPEN_AFTER_BEAT_MS);
  }

  function onState(detail) {
    state.game = String(detail.game || "");
    var found = resolveReel(detail.tutorial, detail.phase, detail.day);
    if (!found) {
      state.reel = null;
      state.key = "";
      state.scoped = "";
      if (btn) btn.hidden = true;
      cancelPendingOpen();
      close();
      return;
    }
    // Compare the GAME-scoped key. Comparing the bare reel key meant
    // starting a second tutorial without a reload looked like "same
    // turn as before" — day 1 planning either way — and nothing opened.
    var scoped = seenKey(found.key);
    var changed = scoped !== state.scoped;
    state.reel = found.reel;
    state.key = found.key;
    state.scoped = scoped;
    ensureButton().hidden = false;
    // Auto-open once per turn. The player asked for the modal to be up
    // by default at the start of a turn; having it reappear on every
    // four-second poll would be a different and much worse feature.
    var wantsOpen = !isSuppressed() && seenSet().indexOf(scoped) === -1;

    if (changed) {
      cancelPendingOpen();
      if (!wantsOpen) { close(); return; }
      // v1.35 — the board gets to finish first. ``busy`` means an orbit
      // is resolving or a night cinematic is running or about to, and
      // the modal covers exactly the part of the screen those happen
      // on. A film about the arsenal filling is worth very little
      // played over the top of the arsenal filling.
      if (detail.busy) { pendingOpen = true; close(); return; }
      openAfterBeat();
      return;
    }

    // Same turn as last time, so the only thing that can have changed
    // is the board going quiet — which is what a held-back film is
    // waiting for.
    if (pendingOpen && !detail.busy && wantsOpen) {
      pendingOpen = false;
      openAfterBeat();
    }
  }

  document.addEventListener("soc:tutorial-state", function (ev) {
    try { onState(ev.detail || {}); }
    catch (e) { console.warn("[tutorial]", e); }
  });

  // Exposed for the film harness, which drives the modal directly rather
  // than waiting on a poll.
  window.socTutorial = {
    open: open,
    close: close,
    reels: REELS,
    resolve: resolveReel,
  };
})();
