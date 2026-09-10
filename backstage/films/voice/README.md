# backstage/films/voice — narration for the teaching films (v1.47)

Offline neural text-to-speech, muxed into the films the tutorial already
ships. Like everything under `backstage/`, none of it is needed to play,
test, fork or score anything — see `../../README.md`.

## Setup

```bash
pip install -r backstage/requirements.txt
cd backstage/films/voice/models
python -m piper.download_voices en_GB-cori-high
```

Models are ~60–110MB each and **gitignored** — identical for everyone
and one command to refetch, so committing them would put 100MB in every
clone to save a download nobody but a film-shooter needs.

## The words are not kept here

There is no script file. The narration **is the caption text already in
`../make_tutorial_films.py`**, read out of its AST.

That is deliberate, and it is the most important decision in this
folder. A separate script file would be a second copy of every sentence
in the tutorial, and copies drift — into the specific failure where the
caption says "right-click any square" while the voice describes a UI
from two versions ago, both halves individually plausible, and the only
witness an attendee who now trusts neither. Reading the shoot script
means a reworded caption is a reworded voice line with no step in
between for anyone to forget.

The cost: a line is only voiceable if it is a literal in the source.
Two currently are not, and `--list` names them rather than guessing.

## The picture waits for the voice

```bash
# 1. what the words cost, before shooting anything
python backstage/films/voice/narrate.py --list
python backstage/films/voice/narrate.py --film basic_probe
python backstage/films/voice/narrate.py --sample     # compare voices

# 2. shoot for narration — captions dwell for as long as their line takes
SOC_BACKEND=memory python run_web.py --no-reload --port 8022 --replace
python backstage/films/make_tutorial_films.py --only basic_probe \
    --base http://127.0.0.1:8022 --voice

# 3. lay the audio in
python backstage/films/voice/mux.py --film basic_probe --dry-run
python backstage/films/voice/mux.py --film basic_probe
```

**Step 2 before step 3, always.** `--voice` makes every stretchable
caption wait for its rendered clip, so the film comes out as long as the
words take. The mux then starts each line exactly when its caption
appears.

This ordering is the whole sync story and it is worth understanding
before changing any of it. Because the shoot consumes the audio's real
duration, there is **one clock**: audio and video cannot drift, by
construction. The obvious alternative — shoot first, then fit narration
into whatever gaps exist — has two clocks that must be kept in
agreement, and it rots quietly, because every unrelated re-shoot moves
the gaps a little and nothing detects it.

`--voice` also lengthens the films, by roughly half again:
`basic_probe` goes 23.0s → 34.2s. That is the honest cost of speaking
words that were written to be read.

## Where narration cannot stretch the film

Captions during a night are cued off the **game clock** — they change
when their hour reaches the screen, because a night's runtime depends on
how many hours carry frames. Padding those waits would put the shoot
behind its own night, so `Film.say()` refuses to (`_on_clock`), and 67
of the 170 lines are in that state.

Those lines have to fit the beat the sim gives them. They mostly do —
the films have far more room than their holds suggest — but `mux.py`
reports any line it had to start late and **fails** past
`DRIFT_LIMIT_S`, on the grounds that a voice two seconds behind is
describing something that already happened. The fix is a shorter
caption, not a cleverer mixer.

## Playback

There is a **narration toggle** in the tutorial modal's footer
(`server/static/tutorial.js`), off by default, remembered per browser in
`soc.tutorial.narrate.v1`.

Three behaviours move together, and each exists for a reason that is not
obvious from the code:

- Every film **starts muted, always**, even with narration remembered
  on. Browsers refuse audible autoplay without a user gesture, and a
  refused video does not play *at all* — so honouring the preference
  eagerly would trade a silent film for a frozen one. It unmutes once
  it may, and if `play()` is still refused the button says "click to
  hear narration" rather than leaving a dead video and no explanation.
- **Looping stops** while narrating. A voice restarting mid-sentence
  every time a 30-second clip comes round is worse than silence.
  Looping is right for a silent clip someone watches three times.
- Turning it on **restarts the film**, because joining a sentence
  halfway is how a player concludes the narration is broken.

The control **hides itself on films with no voice track**, which is
currently most of them. That is asked of the file rather than declared
in a list here, because a list would be wrong the first time somebody
narrates a film and forgets to update it. Chromium only admits to an
audio track once it has decoded some, hence the brief poll in
`stageFor`.

Verify it with `../_probe_narration.py` — every claim above is a browser
fact that Python cannot reach, and all of them fail in the way a player
experiences as "the sound button does nothing".

## Files

| | |
|---|---|
| `narrate.py` | reads the script out of the shoot, renders clips, reports what the words cost |
| `mux.py` | builds one narration track from a cue sheet and lays it into the `.webm` |
| `../_probe_narration.py` | drives the toggle in a real browser |
| `models/` | Piper voices (gitignored) |
| `clips/` | rendered lines, content-addressed so an edit re-renders one (gitignored) |
| `build/` | cue sheets from the last shoot, and assembled tracks (gitignored) |

A cue sheet is written by **every** shoot, narrated or not — it costs a
few hundred bytes and the alternative is re-shooting a film just to find
out when its captions appeared.
