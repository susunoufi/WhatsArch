# WhatsArch - How It Works

## A Simple Explanation

---

# SLIDE 1: The Building Phase (happens once)

### 1. Read Your Chat

```
    +----------------------------------------------+
    |  _chat.txt                                   |
    |  ~~~~~~~~~~~~                                |
    |  [18/11/2018] Su: hi                         |
    |  [18/11/2018] Dad: hey                       |
    |  [20/08/2023] Su: I'm taking apart           |
    |                    Mustafa's crane            |
    |  ...                                         |
    |                         8,302 messages total  |
    +----------------------------------------------+
                           |
                           v
                   +---------------+
                   |   DATABASE    |
                   |   (SQLite)    |
                   +---------------+
```

### 2. Understand Media Files

```
    .-~~~~~-.                                    .------------------------.
   /  VOICE  \        +-------------+           |                        |
  | MESSAGE   | ----> |   WHISPER   | --------> |  "Hey dad, I'm taking  |
  | .opus     |       |  (AI Ears)  |           |   apart the crane      |
   \         /        +-------------+           |   tomorrow"            |
    '-------'                                    '------------------------'

    .---------.                                  .------------------------.
   /           \      +-------------+           |                        |
  |   PHOTO    | ---> |   CLAUDE    | --------> |  "A construction crane |
  |   .jpg     |      |  (AI Eyes)  |           |   on a building site"  |
   \           /      +-------------+           |                        |
    '---------'                                  '------------------------'

    .---------.                                  .------------------------.
   /           \      +-------------+           |                        |
  |   VIDEO    | ---> | CLAUDE +    | --------> |  Visual: "Workers on   |
  |   .mp4     |      | WHISPER     |           |   site near crane"     |
   \           /      +-------------+           |  Audio: "Move it left" |
    '---------'                                  '------------------------'
```

### 3. Group Messages into Chunks

Instead of 8,302 tiny messages, we create **1,334 conversation chunks** (~15 msgs each):

```
                              .-----------------------------------.
    msg #4948  "kitchen       |                                   |
                costs 6200"   |         CHUNK  #821               |
    msg #4949  "call about    |                                   |
                the project"  |    One whole conversation,        |
    msg #4950  "I'm taking    |    not just one lonely message.   |
                apart the     |                                   |
                crane too"    |    ~15 messages that belong       |
    msg #4951  "ok"           |     together by TIME and TOPIC.   |
    msg #4952  "good morning" |                                   |
    ...                       '-----------------------------------'

    Why? Because "ok" alone is useless.
    But "ok" after "I'm taking apart the crane" = meaningful!
```

### 4. Turn Each Chunk into a Vector (Embedding)

```
    +-------------------+       +------------------+       +---------------------+
    |                   |       |                  |       |                     |
    |   CHUNK #821      |       |    E5-LARGE      |       |   [0.23, -0.11,     |
    |   "taking apart   | ----> |    MODEL         | ----> |    0.87, 0.04,      |
    |    the crane of   |       |   (AI Brain)     |       |    ... 1024 numbers]|
    |    Mustafa..."    |       |                  |       |                     |
    +-------------------+       +------------------+       +---------------------+

    Think of it as giving each conversation a SECRET ADDRESS on a giant map.
    Similar conversations live at nearby addresses.
```

---

# SLIDE 2: The Search Phase (every time you ask a question)

```
 .------------------------------------------------------.
 |                                                      |
 |  YOU:  "Did Susu want to take apart the cranes?"     |
 |                                                      |
 '------------------------------------------------------'
                          |
                          v

            +---------------------------+
            |   STEP 1: CLEAN UP        |
            |                           |
            |   Remove filler words:    |
            |   "did" "want" "the"      |
            |                           |
            |   Keep the good stuff:    |
            |   "Susu" "take apart"     |
            |   "cranes"               |
            +---------------------------+
                          |
                          v

 +------------------------------------------------------------+
 |                                                            |
 |   STEP 2: HUNT FOR MATCHING CHUNKS  (5 methods at once!)   |
 |                                                            |
 |   +--------------------------------------------------+    |
 |   |  SEMANTIC SEARCH  (the smart one)                |    |
 |   |                                                  |    |
 |   |  Your question gets a map address too.           |    |
 |   |  Which chunks live NEARBY on the map?            |    |
 |   |                                                  |    |
 |   |  Finds crane talk even if the words are          |    |
 |   |  different! "dismantling" ~ "taking apart"       |    |
 |   +--------------------------------------------------+    |
 |                                                            |
 |   +--------------------------------------------------+    |
 |   |  TEXT SEARCH  (the precise one)                  |    |
 |   |                                                  |    |
 |   |  Literally look for the words:                   |    |
 |   |  "מנופים"  "לפרק"  "סוסו"                       |    |
 |   |  in all 1,334 chunks                             |    |
 |   +--------------------------------------------------+    |
 |                                                            |
 |   +--------------------------------------------------+    |
 |   |  BONUS POINTS                                    |    |
 |   |                                                  |    |
 |   |  Chunks matching 2+ keywords get +3 points       |    |
 |   |  Chunks matching 3+ keywords get +6 points       |    |
 |   +--------------------------------------------------+    |
 |                                                            |
 +------------------------------------------------------------+
                          |
                          v

            +-------------------------------+
            |   STEP 3: PICK THE WINNERS    |
            |                               |
            |   #148  = 37.4 pts            |
            |     crane talk, Nov 2018      |
            |                               |
            |   #821  = 31.8 pts            |
            |     THE crane message!        |
            |     msg #4950 is here         |
            |                               |
            |   #822  = 31.7 pts            |
            |     same + more context       |
            |                               |
            |   #1303 = 28.7 pts            |
            |     selling cranes, 2025      |
            |                               |
            |   #824  = 27.6 pts            |
            |     paying the dismantler     |
            +-------------------------------+
                          |
                          v

   +-------------------------------------------------------+
   |                                                       |
   |   STEP 4: FEED TO THE AI                              |
   |                                                       |
   |   "Hey Claude, here are 5 conversations from          |
   |    Su and Dad's WhatsApp chat.                        |
   |    Read them and answer this question:                |
   |    Did Susu want to take apart the cranes?"           |
   |                                                       |
   +-------------------------------------------------------+
                          |
                          v

   .-------------------------------------------------------.
   |                                                       |
   |  CLAUDE:                                              |
   |                                                       |
   |  "Yes! Su discussed taking apart cranes several       |
   |   times. In message #4950 (Aug 2023) he said          |
   |   he's dismantling Mustafa's crane. Earlier in        |
   |   2018 (#1004) he discussed how cranes could be       |
   |   replaced with telescopic equipment..."              |
   |                                                       |
   '-------------------------------------------------------'
```

---

# The Explanations Page

## What is an Embedding?

Imagine every sentence has a **secret address on a giant map**. Sentences that
mean similar things live at nearby addresses, even if they use completely
different words.

```
    THE MAP OF MEANING
    ~~~~~~~~~~~~~~~~~~

                 "taking apart the crane"  <--- YOU ARE HERE
                          *
                         * *
                        *   *  "dismantling construction equipment"
                       *     *        (NEARBY = similar meaning!)
                      *       *
                     *         *
                    *           *
                   *             *
  "I love pizza"  *               *  "removing the tower crane"
  (FAR AWAY =                        (NEARBY = similar meaning!)
   different topic)
```

The **E5-Large model** reads text and outputs **1,024 numbers** -- those are the
coordinates on this map. The file `chat_chunk_embeddings.npy` stores all 1,334 map
addresses (one per chunk).

When you ask a question, your question **also** gets a map address. Then we just find
which chunks are **closest to your question on the map**. That's semantic search.

## What is a Chunk?

A single WhatsApp message like "ok" is meaningless on its own. You need the
conversation around it. A **chunk** groups ~15 consecutive messages into one
piece of conversation.

```
    USELESS ALONE:                  USEFUL AS A CHUNK:

    +-----------+                   +----------------------------------+
    |   "yes"   |  what             | Dad: "Are you coming tomorrow?"  |
    +-----------+  does             | Su:  "Yes"      <-- NOW it means |
    |   "ok"    |  this             |                     something!   |
    +-----------+  even             | Dad: "Bring the tools"           |
    |   "lol"   |  mean??           | Su:  "Ok"       <-- context!    |
    +-----------+                   | Su:  "lol you said tools"        |
                                    +----------------------------------+
```

Chunks **overlap by 5 messages**, so messages near a boundary appear in 2 chunks.
Nothing falls through the cracks.

## What is RAG?

**R**etrieval **A**ugmented **G**eneration. Fancy name, dead simple idea:

```
    +-------------+     +---------------+     +--------------+
    |             |     |               |     |              |
    | 1. RETRIEVE | --> | 2. AUGMENT    | --> | 3. GENERATE  |
    |             |     |               |     |              |
    | Find the    |     | Paste them    |     | AI reads the |
    | relevant    |     | into the AI's |     | context and  |
    | chunks      |     | prompt as     |     | writes an    |
    |             |     | context       |     | answer       |
    +-------------+     +---------------+     +--------------+
```

Without RAG, the AI knows **nothing** about your chats.
With RAG, we **stuff the relevant conversations into its mouth** right before asking.

The AI never memorizes your chats. Every time you ask, we search fresh.
That's why search quality matters so much -- garbage in, garbage out.

## What is FTS5 / Trigram?

A fast text search engine inside SQLite. **Trigram** means it breaks every word
into overlapping 3-letter pieces:

```
    "מנופים"  --->  [ מנו ]  [ נופ ]  [ ופי ]  [ פים ]

    Search for "מנוף"?
                      [ מנו ]  [ נוף ]
                        ^
                        |
                    MATCH! shares "מנו" with "מנופים"
```

This works for **any language** without understanding grammar.
No need to teach it Hebrew rules -- it just matches letter patterns.

## The Full Picture (2 lines!)

```
    BUILD (once):      WhatsApp Export --> Transcribe/Describe --> Parse
                       --> Database --> Cut into Chunks --> Embed Chunks

    SEARCH (each Q):   Question --> Find best chunks --> Feed to AI --> Answer
```

That's the whole system. Everything else is just details.
