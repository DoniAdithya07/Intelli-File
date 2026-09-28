# IntelliFile 1.0.0: what works, measured

Every number on this page was measured on 27 September 2026 on the development laptop (12th-gen Core i5, 16 GB), offline, with the commands listed at the end. To check again at any time, run the feature check (or ask the `feature-checker` agent in Claude Code): it reports each feature as PASS or FAIL and explains any failure.

## At a glance

| Feature | Result | Measured |
|---|---|---|
| Finding files by meaning, words, name and content | Working | 10 of 10 checks; right file in the top 5 for 100% of 60 test searches |
| Search speed | Working | typical search 20 to 45 ms; slowest 5% under 190 ms (limit 1 s) |
| Choosing the search method (router) | Working | same accuracy as always using the full method (100%) at 77% of its time; right method for 60 of 60 searches |
| Photos: find a picture by describing it | Working, with limits | drawn test pictures 5 of 5; 26 real photos: right picture first 50%, in the top 3 78% |
| Videos: find the video and the moment | Working | right video 6 of 6 (100%); right video and moment 5 of 6 (83%) |
| Words inside screenshots (OCR) | Working | found; a picture needs at least 5 readable words |
| Spoken words in recordings | Working | found; silent recordings get no invented text |
| Ask: answers from your files | Working, with limits | answer backed by its sources 25 of 25 (100%); right file cited 23 of 25 (92%); "not found" when the answer is not there 5 of 5 (100%); false answers 0 |
| Ask: speed | Working | inside the 50 s limit every time (slowest 42.6 s on a busy laptop) |
| Ask: questions about your collection | Working | "how many files", "any videos?", "how many photos?" answered exactly from the index |
| Personalization | Working | the file you use first for 100% of ambiguous searches (39% without it); 0 other searches made worse |
| Offline and read-only | Working | 0 network attempts in every test; test files unchanged byte for byte |
| Full automated test suite | 23 of 24 passed, 1 flaky | the power test passes on its own; under heavy memory load its 60 s wait was too short |
| App smoke test (the real app, clicked through) | Working | 18 of 18 checks |

## Photos and videos in detail

The drawn shapes prove the wiring; real photographs show the accuracy. The test uses the 26 photographs every Windows PC ships in `C:\Windows\Web`: lakes, mountains, sunsets, cherry blossoms and abstract 3D art, several of them near-duplicates.

- **Natural scenes are found reliably:** "sunset over a lake", "a calm lake with mountains and a forest", "snowy mountains with pine trees next to a lake", "a pale pink desert landscape with a lake" all found the right photo first.
- **Near-identical abstract art is the weak spot:** "a plain solid blue background" found the blue waves first; the two blue flower wallpapers are often swapped. This is a limit of the photo model (CLIP), not of the app's code.
- **Extra caption phrasings were tested and did not help** (same 11 of 18 first, 16 of 18 in the top 3 when ranked by the model alone), so the setting was left unchanged.
- **Honest confidence:** the app hides results it is unsure of rather than show wrong ones. For the two hardest descriptions it also hid the right picture; ranked by the model alone, the right picture is in the top 3 for 16 of 18.

## Ask in detail

- Answers only from your files, and checks itself: citations must support the answer, every number must appear in the sources, and the question's premise must be in the files; otherwise it says "I couldn't find that in your files".
- It needs files that contain the answer. On a computer where only a few files are indexed (for example program settings files), it cannot answer questions about notes that are not there; the Index page shows what is indexed.
- Fixed on 27 September: answers that ran past the 50 s limit (too many sources were read before writing), the self-check that could run late, questions about the collection itself, and silent recordings indexed as "you".

## Known limits

- Photo search on near-identical pictures (see above).
- Ask on a busy laptop takes 20 to 45 s; on a free one about 14 s.
- The photo page has no side preview with the picture's text yet, and screens do not have their own Help links (UI_DESIGN sections 3 and 13).
- The first start after extracting the zip can take up to a minute while Windows scans the new program files.

## Checked by a person, not by a script

- Voice search with a real microphone.
- Unplugging the laptop while it indexes (pauses, then resumes).
- Ctrl+Space from another program.
- The zip on a second, clean Windows PC.

## How these numbers were produced

From `backend` with `INTELLIFILE_OFFLINE_GUARD=1`:

| Measure | Command |
|---|---|
| Feature check (files, photos, videos, audio, safety) | `scripts\check_features.py` and `--packaged <unzipped IntelliFile>` |
| Real photos and videos | `scripts\evaluate_visual_real.py` |
| Search accuracy and speed | `scripts\evaluate_retrieval.py` |
| Router | `scripts\evaluate_routing.py` |
| Ask (30 questions) | `scripts\evaluate_agent.py` |
| Personalization | `scripts\evaluate_personalization.py` |
| Everything | `scripts\run_all_phases.py` |

Results are also written to `backend\data\eval_*.json`.
