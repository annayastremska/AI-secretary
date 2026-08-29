# -*- coding: utf-8 -*-
"""ЗМІСТ слайдів. Кожен факт узятий з опису проєкту, не з голови.

Джерело в дужках біля кожного блока -- розділ docx. Що не з docx, те зміряно
сьогодні на живій системі й теж названо.

ЯК ВІДБИРАЛОСЬ. З розділу «Чого система не робить» у docx дев'ять пунктів, з
«Що далі» -- дев'ять. На слайд не йдуть усі: беруться ті, що (а) зрозумілі
людині, яка бачить проєкт уперше, (б) мають наслідок для роботи частини, а не
для нашого коду. Тому «одна дорога відповіді замість двох» на слайд не пішла --
це наша внутрішня заборгованість, а «перетин періодів на завантаженні» пішла:
у ній видно наслідок.
"""

TITLE = dict(
    name="AI-Secretary",
    sub="Personnel records, answered from the unit&#39;s own documents.",
    meta=["Demo Day &middot; 30 August 2026",
          "Live system on the QR code"],
)

#: Розділ 9 docx. Чотири з дев'яти -- ті, що мають наслідок для частини.
LIMITS = [
    ("documents, not people",
     "The system knows what is written down. If someone came back early and "
     "no one wrote it, it does not know."),
    ("one person, two places",
     "Refused when the system <b>creates</b> a document. Accepted when it "
     "<b>receives</b> one &#8212; two such pairs sit in the database now."),
    ("lawful or not, same look",
     "A replacement slip says &#8220;instead of &#8470;118&#8221;. We read "
     "that line and do not store it, so we cannot tell a replacement from a "
     "contradiction."),
    ("nothing closes the queue",
     "31 documents wait for a person. There is no screen to confirm them, "
     "and no way to close a remark."),
]

#: Розділ 10 docx. Чотири, у яких наслідок видно без пояснень.
NOT_YET = [
    ("a queue screen", "So a draft can be confirmed later, not only at "
                       "upload."),
    ("the overlap check on upload",
     "Write it as a draft, put a remark in the queue, and store the "
     "&#8220;instead of &#8470;&#8230;&#8221; link."),
    ("roles, not table lists",
     "A guest with the QR code currently sees what an operator sees."),
    ("containers", "Everything runs as one service on the host today."),
]

#: Розділ 1 і 8 docx: обіцянка й цифри.
PROMISED = [
    ("every answer has four parts",
     "date &middot; denominator &middot; drafts &middot; source document",
     False),
    ("a draft is not a fact",
     "132 of 2011 facts stay out of every count", False),
    ("refusal beats invention",
     "measured &#8212; the test set marks questions whose right answer is "
     "&#8220;no&#8221;", False),
    ("a corpus, not a sample",
     "204 documents &middot; 303 people &middot; 44 acts", False),
    ("field extraction, against known answers",
     "953 of 953 on 82 documents", False),
    ("three to five document types",
     "two &#8212; narrowed out loud, not quietly", True),
]

#: Розділ 6 docx: чотири яруси з реальними часами.
TIERS = [
    ("1 &middot; rules",
     "wording recognised by pattern &#8212; <b>0.03&#8211;0.8 s</b>, the "
     "model is not called"),
    ("2 &middot; similarity",
     "the question matches a known example closely enough"),
    ("3 &middot; the model picks",
     "it names one query from the list and fills in the parameters"),
    ("4 &middot; the model writes",
     "a single read, checked before it runs &#8212; when no query fits"),
]

#: Розділ 7 docx: прилади. Три числа, які щось доводять.
TESTED = [
    "Three full runs, about eighty questions &#8212; <b>31 defects</b>, found "
    "by us before anyone asked.",
    "127 questions: paraphrases, typos, slang, traps, and questions whose "
    "correct answer is a refusal.",
    "Zero confidently wrong routings. 15 of 15 person lookups. 12 of 12 "
    "counts.",
    "Every fixed defect is pinned by its own test; the whole set runs again "
    "after each change.",
]

#: Розділ 9 docx: доступ до даних. Найважливіше -- що це РІШЕННЯ, не дірка.
DATA = [
    ("where it runs",
     "Model, database and site on one machine of ours. No external service "
     "in the path."),
    ("what the site may do",
     "It connects to the database <b>read-only</b> and cannot write."),
    ("personal data is visible",
     "By design. In a unit, the people who work with these documents already "
     "have that access, and need it."),
    ("the real boundary is who logged in",
     "And it is coarse: a guest with the QR code sees what an operator sees."),
]

#: Розділ 3 і 10 docx: що вимагає розробника.
EXTENDING = [
    "A new kind of question: one more verified query &#8212; about an hour.",
    "An officer cannot add either one alone.",
    "Already changeable without code: ranks, regulation topics, what the "
    "model is told, the examples it matches against.",
]

#: Розділ 7.7 docx: стрес-тест гардів, 48 атак.
MODEL = [
    ("the query picker", "Held all six attacks. Its reply must fit a fixed "
                         "shape, so there is no room for a free sentence."),
    ("the citation gate", "Held all six. A quote must appear word for word "
                          "in the document."),
    ("the bare model", "Does break under role-play framing. That weakness is "
                       "upstream of us, not in the product."),
    ("why the product holds",
     "No channel exists where free model text reaches a person: every call "
     "returns a fixed shape, and answers are written by code."),
]
