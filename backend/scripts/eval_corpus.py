"""The evaluation corpus and labelled query set for Phase 20.

Generated, not collected: 40 short documents across six areas of one
person's life (cooking, fitness, money, travel, engineering notes, home),
in the file types the app indexes, plus a labelled set of 60 queries
spread over the router's tiers. Everything is deterministic so the
numbers in the README can be reproduced on any machine with
`scripts/evaluate_routing.py` and `scripts/evaluate_personalization.py`.

Each query carries the file(s) that count as relevant. Multi-part queries
list both files; hit@k counts the first relevant one, and `both@5` in the
routing report says whether both appeared in the top 5.
"""

from pathlib import Path

DOCS: dict[str, str] = {
    # cooking
    "sourdough.md": "Feed the sourdough starter with flour and water twice a day. Bulk ferment the dough for five hours at 24 C, then bake at 230 C in a dutch oven, 20 minutes covered and 25 uncovered.",
    "croissants.txt": "Laminate cold butter into the dough with three folds, resting 30 minutes in the fridge between folds. Proof the croissants until puffy, egg wash, bake at 200 C for 18 minutes.",
    "rasmalai recipe.txt": "Rasmalai: curdle full-fat milk with lemon, press the chenna, knead smooth, shape discs and simmer in sugar syrup. Chill in saffron milk with cardamom and pistachio.",
    "weeknight curry.md": "Weeknight chickpea curry: onion, garlic, ginger, a tin of tomatoes, a tin of chickpeas, garam masala. Twenty minutes, serves four, freezes well.",
    "coffee notes.txt": "Pour-over ratio 1:16, water at 94 C, 30 second bloom. The Ethiopian beans taste of blueberry; grind slightly finer for the V60.",
    "pizza dough.md": "Pizza dough at 65 percent hydration, 00 flour, 48 hour cold ferment in the fridge. Stretch by hand, never roll; bake on a steel at the oven's maximum.",
    # fitness
    "gym plan.txt": "Gym plan for the week. Monday: chest and triceps - bench press, incline dumbbell press, dips. Wednesday: back and biceps - deadlifts, pull-ups, barbell rows. Friday: legs and shoulders - squats, lunges, overhead press. Cardio on Tuesday and Thursday, 30 minutes.",
    "running log.csv": "date,distance_km,minutes,notes\n2025-03-02,5.0,27,easy\n2025-03-05,8.2,44,tempo\n2025-03-09,12.0,68,long run along the river\n2025-03-12,5.0,26,easy",
    "half marathon plan.md": "Twelve-week half marathon plan: three runs a week, long run grows from 10 to 19 km, one tempo session, taper in the last two weeks. Race day 18 May.",
    "physio exercises.txt": "Physio exercises for the left knee: wall sits 3 x 45 seconds, single-leg balance, clamshells with the green band, step-downs from a low box. Twice daily for six weeks.",
    "swimming drills.md": "Swimming drills: catch-up freestyle, fingertip drag, 6-kick switch. Main set 10 x 100 m on 2:00. Breathe every three strokes to balance the stroke.",
    # money
    "monthly expenses.csv": "month,category,amount,note\nJanuary,rent,1200,paid on the 3rd\nJanuary,groceries,340,mostly farmers market\nFebruary,gym membership,45,annual plan renewed\nMarch,car insurance,610,six-month premium",
    "invoices/march invoice.txt": "Invoice 2025-03: consulting services, 12 hours. Payment terms net 30, so it is due on 14 April 2025; the amount is 1,440 euros.",
    "invoices/april invoice.txt": "Invoice 2025-04: workshop facilitation, two days. Amount 2,200 euros, due 30 May 2025. Purchase order PO-8812.",
    "insurance policy.txt": "Car insurance policy number CI-4481-Z. Premium 610 euros for six months, renewal due 30 September. Excess 350 euros. Claims line 0800 555 0199.",
    "tax notes.md": "Tax return notes: home office deduction is 15 percent of rent, keep the invoices folder as evidence, deadline for filing is 31 October. Accountant: Mrs Lindqvist.",
    "savings plan.txt": "Savings plan: 400 euros a month into the index fund on the 1st, emergency fund target six months of expenses, review every quarter.",
    # travel
    "lisbon trip.md": "Lisbon trip, 12 to 16 May. Flights TAP 1234 out at 07:40, back on the 16th at 19:10. Hotel Alfama Suites, booking ref AS-77Q. Day trip to Sintra on the 14th.",
    "kyoto itinerary.txt": "Kyoto itinerary: Fushimi Inari at dawn, Arashiyama bamboo grove, Nishiki market for lunch, tea ceremony at 15:00. Ryokan Hoshinoya, check-in from 15:00. JR pass valid from the 3rd.",
    "packing list.txt": "Packing list for the trek: rain jacket, headlamp, two litres of water, sunscreen, the first aid kit from the garage, and the paper map in case the phone dies.",
    "travel insurance.md": "Travel insurance certificate TI-2290, covers medical up to 1 million, cancellation up to 3,000 euros, valid 1 May to 31 August. Emergency assistance +44 20 7946 0000.",
    "camping trip.md": "Camping at Loch Lomond, 21 to 23 June. Pitch 14 at Cashel campsite booked. Bring the two-person tent, the stove, and check the midge forecast.",
    # engineering
    "scaling_notes.md": "Horizontal scaling allows additional server instances to be provisioned when traffic demand increases, distributed by a load balancer. Autoscaling policies react to CPU load and request latency.",
    "queue_design.md": "A message queue decouples producers from consumers; consumer groups scale horizontally and partitions preserve ordering. Dead-letter queues hold messages that fail repeatedly.",
    "caching strategy.txt": "Cache-aside: the application reads the cache first, falls back to the database on a miss and writes the value back with a TTL. Invalidate on write to avoid stale reads.",
    "incident report.md": "Incident 2025-02-11: the checkout service ran out of database connections under a flash sale. Root cause: connection pool sized for 50 while 400 workers were running. Fix: pool per worker, plus a circuit breaker.",
    "meeting notes.txt": "Team meeting 4 April. Decisions: move the release to 22 April, Priya owns the migration runbook, Tom to draft the on-call rota by Friday. Risks: the vendor API rate limit.",
    "deploy checklist.md": "Deploy checklist: run the migrations in a transaction, warm the cache, canary 5 percent for 15 minutes, watch p99 latency and error rate, then roll to 100 percent.",
    "api design.md": "API design guidelines: nouns for resources, plural, versioned under /v1, pagination with cursors not offsets, idempotency keys on every POST that creates something.",
    # home
    "landlord letter.txt": "Dear Mr Okafor, the kitchen tap has been dripping since the second week of March and the bathroom extractor fan no longer switches on. Please arrange a repair visit. Kind regards, Abhishek.",
    "lease agreement.txt": "Lease agreement for Flat 3, 18 Harbour Street. Term 12 months from 1 February 2025. Rent 1,200 euros monthly, deposit 1,800 euros held in the deposit scheme. Notice period two months.",
    "garden plan.md": "Garden plan: tomatoes and basil in the raised bed by the fence, courgettes in the big pots, sow the wildflower strip in April. Compost bin behind the shed.",
    "wifi setup.txt": "Wifi setup: router admin at 192.168.1.1, network name HarbourNet, 5 GHz for the office, guest network for visitors. Change the admin password after the engineer visit.",
    "shopping list.txt": "Shopping list: oat milk, eggs, spinach, lentils, olive oil, dishwasher tablets, and a birthday card for Rahul.",
    "birthday party.md": "Rahul's birthday party on 7 June at the community hall, 30 guests, order the cake from the bakery on Mill Road, playlist on the shared speaker, bouncy castle from 14:00.",
    "reading list.md": "Reading list: Designing Data-Intensive Applications, The Pragmatic Programmer, Salt Fat Acid Heat, and Kitchen Confidential.",
    "car service.txt": "Car service booked for 9 October at Miller's garage: oil change, brake pads front, MOT. Estimated 320 euros. Drop off at 08:00.",
    "dentist.txt": "Dentist appointment 14 July at 10:30, Dr Ahmed, bring the insurance card. Next hygienist visit in six months.",
    "book club.md": "Book club meets the first Thursday of the month at Nina's. This month: The Remains of the Day. Bring something to share; I'm on dessert.",
}

# (query, [relevant files], tier the router is expected to pick)
QUERIES: list[tuple[str, list[str], str]] = [
    # --- filename lookups (names typed as people remember them) ---
    ("gym plan", ["gym plan.txt"], "filename"),
    ("lisbon trip", ["lisbon trip.md"], "filename"),
    ("landlord letter", ["landlord letter.txt"], "filename"),
    ("monthly expenses", ["monthly expenses.csv"], "filename"),
    ("kyoto itinerary", ["kyoto itinerary.txt"], "filename"),
    ("insurance policy", ["insurance policy.txt"], "filename"),
    ("packing list", ["packing list.txt"], "filename"),
    ("deploy checklist", ["deploy checklist.md"], "filename"),
    ("incident report", ["incident report.md"], "filename"),
    ("half marathon plan", ["half marathon plan.md"], "filename"),
    ("rasmalai recipe", ["rasmalai recipe.txt"], "filename"),
    ("march invoice", ["invoices/march invoice.txt"], "filename"),
    # --- metadata filters (alone or with words) ---
    ("type:csv", ["running log.csv", "monthly expenses.csv"], "metadata"),
    ("in:invoices", ["invoices/march invoice.txt", "invoices/april invoice.txt"], "metadata"),
    ("type:csv expenses", ["monthly expenses.csv"], "keyword"),
    ("in:invoices workshop", ["invoices/april invoice.txt"], "keyword"),
    ("type:txt insurance", ["insurance policy.txt"], "keyword"),
    ("type:md scaling", ["scaling_notes.md"], "keyword"),
    ("ext:csv river", ["running log.csv"], "keyword"),
    ("type:txt dentist", ["dentist.txt"], "keyword"),
    # --- short keyword queries ---
    ("sourdough starter", ["sourdough.md"], "keyword"),
    ("deposit scheme", ["lease agreement.txt"], "keyword"),
    ("autoscaling policies", ["scaling_notes.md"], "keyword"),
    ("dead-letter queue", ["queue_design.md"], "keyword"),
    ("circuit breaker", ["incident report.md"], "keyword"),
    ("cardamom pistachio", ["rasmalai recipe.txt"], "keyword"),
    ("midge forecast", ["camping trip.md"], "keyword"),
    ("idempotency keys", ["api design.md"], "keyword"),
    ("wall sits", ["physio exercises.txt"], "keyword"),
    ("bouncy castle", ["birthday party.md"], "keyword"),
    ("oat milk", ["shopping list.txt"], "keyword"),
    ("guest network", ["wifi setup.txt"], "keyword"),
    ("brake pads", ["car service.txt"], "keyword"),
    ("emergency fund", ["savings plan.txt"], "keyword"),
    # --- natural language, few shared words (meaning needed) ---
    ("how do we add capacity when lots of visitors arrive at once", ["scaling_notes.md"], "hybrid"),
    ("what should I do about the leaking tap in the flat", ["landlord letter.txt"], "hybrid"),
    ("how long does the bread dough need to rise", ["sourdough.md"], "hybrid"),
    ("what went wrong during the big sale", ["incident report.md"], "hybrid"),
    ("which exercises help my knee recover", ["physio exercises.txt"], "hybrid"),
    ("what is my hotel confirmation for portugal", ["lisbon trip.md"], "hybrid"),
    ("how much do I put aside each month", ["savings plan.txt"], "hybrid"),
    ("when is the car due at the mechanic", ["car service.txt"], "hybrid"),
    ("what do I need to bring for the hike", ["packing list.txt"], "hybrid"),
    ("how should the shop's pages be released safely", ["deploy checklist.md"], "hybrid"),
    ("what temperature is the water for the coffee", ["coffee notes.txt"], "hybrid"),
    ("who is responsible for the database move", ["meeting notes.txt"], "hybrid"),
    ("how do we keep old values out of the cache", ["caching strategy.txt"], "hybrid"),
    ("what am I planting near the fence", ["garden plan.md"], "hybrid"),
    ("which book is the group reading", ["book club.md"], "hybrid"),
    ("how many guests are coming to the celebration", ["birthday party.md"], "hybrid"),
    # --- multi-part questions ---
    ("when is the rent due and how much is the deposit, and what is the notice period?", ["lease agreement.txt"], "hybrid"),
    ("what do I train on wednesday, and how long is the long run in the marathon plan?", ["gym plan.txt", "half marathon plan.md"], "hybrid"),
    ("when do I fly to lisbon and what is the travel insurance certificate number?", ["lisbon trip.md", "travel insurance.md"], "hybrid"),
    ("how much is the march invoice and when is the april one due?", ["invoices/march invoice.txt", "invoices/april invoice.txt"], "hybrid"),
    ("what caused the checkout incident, and what does the deploy checklist say about canaries?", ["incident report.md", "deploy checklist.md"], "hybrid"),
    ("what is the wifi admin address and what should I change after the engineer visit?", ["wifi setup.txt"], "hybrid"),
    ("what is the insurance excess, and when is the policy renewal?", ["insurance policy.txt"], "hybrid"),
    ("what temperature do I bake the sourdough at, and how long do croissants bake?", ["sourdough.md", "croissants.txt"], "hybrid"),
    ("who owns the migration runbook, and when is the release date?", ["meeting notes.txt"], "hybrid"),
    ("what is the tax filing deadline and what percentage of rent can I deduct?", ["tax notes.md"], "hybrid"),
]


def write_corpus(root: Path) -> None:
    for name, text in DOCS.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
