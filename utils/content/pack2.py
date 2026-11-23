"""Extra variety for the games & fun commands (text only, appended to the existing pools so nothing that already exists changes)."""
from __future__ import annotations


def merge(old, new):
    """old + the items of new that are not in old yet (order kept; old items keep their index, so existing buttons keep working)."""
    seen = set(map(repr, old))
    return list(old) + [x for x in new if repr(x) not in seen]


def merge_dict(old: dict, new: dict) -> dict:
    out = {k: list(v) for k, v in old.items()}
    for k, v in new.items():
        out[k] = merge(out.get(k, []), v)
    return out

# ---- 🧠 trivia: (question, [4 options, the right one FIRST - they are shuffled when shown], 0)
_T = [
    ("Which gas do plants take in from the air?", "Carbon dioxide", "Oxygen", "Nitrogen", "Hydrogen"),
    ("How many continents are there on Earth?", "7", "5", "6", "8"),
    ("Which is the largest planet in our solar system?", "Jupiter", "Saturn", "Neptune", "Earth"),
    ("What is the capital of Japan?", "Tokyo", "Kyoto", "Osaka", "Nagoya"),
    ("Who painted the Mona Lisa?", "Leonardo da Vinci", "Michelangelo", "Raphael", "Vincent van Gogh"),
    ("Which is the longest river in India?", "Ganga", "Yamuna", "Godavari", "Brahmaputra"),
    ("What is H2O commonly called?", "Water", "Salt", "Hydrogen peroxide", "Ammonia"),
    ("Which animal is known as the 'Ship of the Desert'?", "Camel", "Horse", "Elephant", "Donkey"),
    ("How many players are on a football (soccer) team on the pitch?", "11", "9", "10", "12"),
    ("Which festival is called the Festival of Lights?", "Diwali", "Holi", "Eid", "Pongal"),
    ("What is the largest mammal in the world?", "Blue whale", "African elephant", "Giraffe", "Hippopotamus"),
    ("Which metal is liquid at room temperature?", "Mercury", "Iron", "Copper", "Silver"),
    ("Who is known as the Father of the Nation in India?", "Mahatma Gandhi", "Jawaharlal Nehru", "Sardar Patel", "Subhas Chandra Bose"),
    ("Which planet has the most visible rings?", "Saturn", "Mars", "Venus", "Mercury"),
    ("What is the square root of 144?", "12", "14", "16", "10"),
    ("Which country is famous for the Eiffel Tower?", "France", "Italy", "Spain", "Germany"),
    ("How many days are there in a leap year?", "366", "365", "364", "367"),
    ("Which bird is a symbol of peace?", "Dove", "Eagle", "Sparrow", "Crow"),
    ("What do bees make?", "Honey", "Milk", "Silk", "Wax paper"),
    ("Which is the smallest prime number?", "2", "1", "3", "0"),
    ("Which organ pumps blood around the body?", "Heart", "Lungs", "Liver", "Kidney"),
    ("In which sport would you perform a 'slam dunk'?", "Basketball", "Volleyball", "Tennis", "Golf"),
    ("What is the national animal of India?", "Bengal tiger", "Lion", "Elephant", "Leopard"),
    ("Which planet is called the Morning Star?", "Venus", "Mars", "Jupiter", "Mercury"),
    ("How many sides does a hexagon have?", "6", "5", "7", "8"),
    ("Which ocean lies between Africa and Australia?", "Indian Ocean", "Atlantic Ocean", "Pacific Ocean", "Arctic Ocean"),
    ("Who wrote the play 'Romeo and Juliet'?", "William Shakespeare", "Charles Dickens", "Mark Twain", "Jane Austen"),
    ("What is the hardest natural substance?", "Diamond", "Gold", "Iron", "Quartz"),
    ("Which vitamin do we get from sunlight?", "Vitamin D", "Vitamin C", "Vitamin A", "Vitamin B12"),
    ("What is the capital of Canada?", "Ottawa", "Toronto", "Vancouver", "Montreal"),
    ("Which instrument has black and white keys?", "Piano", "Guitar", "Flute", "Violin"),
    ("How many colours are there in a rainbow?", "7", "5", "6", "8"),
    ("Which country gifted the Statue of Liberty to the USA?", "France", "England", "Spain", "Italy"),
    ("What is the fastest land animal?", "Cheetah", "Lion", "Horse", "Leopard"),
    ("Which is the highest mountain peak in the world?", "Mount Everest", "K2", "Kangchenjunga", "Mount Kilimanjaro"),
    ("What do we call a baby kangaroo?", "Joey", "Cub", "Calf", "Pup"),
    ("Which gas do humans need to breathe in to live?", "Oxygen", "Helium", "Methane", "Carbon monoxide"),
    ("Which is the national flower of India?", "Lotus", "Rose", "Marigold", "Jasmine"),
    ("How many minutes are in two hours?", "120", "100", "90", "150"),
    ("Which planet do we live on?", "Earth", "Mars", "Venus", "Saturn"),
    ("What is the currency of Japan?", "Yen", "Won", "Yuan", "Dollar"),
    ("Who discovered gravity after seeing an apple fall (as the story goes)?", "Isaac Newton", "Albert Einstein", "Galileo", "Nikola Tesla"),
    ("Which language has the most native speakers in the world?", "Mandarin Chinese", "English", "Hindi", "Spanish"),
    ("Taj Mahal is located in which city?", "Agra", "Delhi", "Jaipur", "Lucknow"),
    ("What is the chemical symbol for gold?", "Au", "Ag", "Gd", "Go"),
    ("Which sea creature has eight arms?", "Octopus", "Starfish", "Jellyfish", "Crab"),
    ("Which is the biggest desert in the world (by area)?", "Antarctic Desert", "Sahara", "Gobi", "Thar"),
    ("In cricket, how many balls are in one over?", "6", "5", "8", "10"),
    ("Which is the only planet known to support life?", "Earth", "Mars", "Titan", "Venus"),
    ("What is the capital of Italy?", "Rome", "Milan", "Venice", "Naples"),
    ("Who invented the telephone?", "Alexander Graham Bell", "Thomas Edison", "Nikola Tesla", "Guglielmo Marconi"),
    ("Which part of the plant makes food using sunlight?", "Leaf", "Root", "Stem", "Flower"),
    ("How many bones are in an adult human body?", "206", "186", "250", "300"),
    ("What is the largest country in the world by area?", "Russia", "Canada", "China", "USA"),
    ("Which animal is the tallest in the world?", "Giraffe", "Elephant", "Camel", "Ostrich"),
    ("Which dish is made of rice, spices and meat or vegetables and is loved across India?", "Biryani", "Pasta", "Sushi", "Tacos"),
    ("Which planet is nearest to Earth's size and is called Earth's twin?", "Venus", "Mars", "Neptune", "Mercury"),
    ("What is the name of the galaxy we live in?", "Milky Way", "Andromeda", "Whirlpool", "Sombrero"),
    ("Which shape has three sides?", "Triangle", "Square", "Pentagon", "Circle"),
    ("Who was the first person to walk on the Moon?", "Neil Armstrong", "Buzz Aldrin", "Yuri Gagarin", "Rakesh Sharma"),
    ("What is the freezing point of water in Celsius?", "0", "32", "100", "-10"),
    ("Which famous tower leans in Italy?", "Tower of Pisa", "Eiffel Tower", "Big Ben", "CN Tower"),
    ("What do you call a group of lions?", "Pride", "Pack", "Herd", "Flock"),
    ("Which season comes after summer in the northern hemisphere?", "Autumn", "Winter", "Spring", "Monsoon"),
    ("Which body part helps you see?", "Eyes", "Ears", "Nose", "Tongue"),
    ("What is 15 x 4?", "60", "45", "50", "65"),
    ("Which fruit is known as the 'King of Fruits' in India?", "Mango", "Apple", "Banana", "Guava"),
    ("Which country has the maple leaf on its flag?", "Canada", "Australia", "Norway", "Sweden"),
    ("What is the main language spoken in Brazil?", "Portuguese", "Spanish", "English", "French"),
    ("What is the boiling point of water in Celsius at sea level?", "100", "90", "80", "120"),
    ("Which planet is known for its Great Red Spot?", "Jupiter", "Mars", "Saturn", "Uranus"),
    ("Who composed the Indian national song 'Vande Mataram'?", "Bankim Chandra Chatterjee", "Rabindranath Tagore", "Iqbal", "Subramania Bharati"),
    ("Which device is used to see very far away stars?", "Telescope", "Microscope", "Periscope", "Stethoscope"),
    ("Which is the smallest continent?", "Australia", "Europe", "Antarctica", "South America"),
    ("How many strings does a standard guitar have?", "6", "4", "5", "8"),
    ("Which famous scientist developed the theory of relativity?", "Albert Einstein", "Isaac Newton", "Stephen Hawking", "Marie Curie"),
    ("What is the capital of Egypt?", "Cairo", "Alexandria", "Giza", "Luxor"),
    ("Which meal is eaten in the morning?", "Breakfast", "Lunch", "Dinner", "Supper"),
    ("Which sea is the saltiest and lets you float easily?", "Dead Sea", "Red Sea", "Black Sea", "Baltic Sea"),
    ("Which Indian city is known as the 'Pink City'?", "Jaipur", "Jodhpur", "Udaipur", "Agra"),
    ("Which Indian city is known as the 'City of Joy'?", "Kolkata", "Mumbai", "Chennai", "Varanasi"),
    ("Which is the largest state of India by area?", "Rajasthan", "Madhya Pradesh", "Maharashtra", "Uttar Pradesh"),
    ("Which dance form is famous from Odisha?", "Odissi", "Kathak", "Bharatanatyam", "Bihu"),
    ("Which famous temple chariot festival is held in Puri?", "Rath Yatra", "Kumbh Mela", "Onam", "Durga Puja"),
    ("Which sweet from Odisha is famous worldwide?", "Rasabali", "Jalebi", "Petha", "Mysore Pak"),
    ("What is the capital of Odisha?", "Bhubaneswar", "Cuttack", "Puri", "Rourkela"),
    ("Which gas makes up most of Earth's atmosphere?", "Nitrogen", "Oxygen", "Carbon dioxide", "Argon"),
    ("What is the process by which water turns into vapour called?", "Evaporation", "Condensation", "Freezing", "Melting"),
    ("Which planet spins on its side?", "Uranus", "Mars", "Earth", "Neptune"),
    ("Which blood cells carry oxygen?", "Red blood cells", "White blood cells", "Platelets", "Plasma"),
    ("Who is the author of the Harry Potter series?", "J. K. Rowling", "Roald Dahl", "Enid Blyton", "C. S. Lewis"),
    ("In which year did India gain independence?", "1947", "1950", "1942", "1935"),
    ("Which is the largest bone in the human body?", "Femur", "Skull", "Spine", "Rib"),
    ("How many hours are there in a week?", "168", "144", "156", "180"),
    ("What do caterpillars turn into?", "Butterflies", "Beetles", "Ants", "Bees"),
    ("Which animal lays the largest eggs?", "Ostrich", "Eagle", "Crocodile", "Penguin"),
    ("Which is the closest star to Earth?", "The Sun", "Proxima Centauri", "Sirius", "Polaris"),
    ("What is the opposite of 'ancient'?", "Modern", "Old", "Antique", "Historic"),
    ("Which popular messaging app has a paper-plane logo?", "Telegram", "WhatsApp", "Signal", "Line"),
    ("Which shape is a football (soccer) pitch usually?", "Rectangle", "Circle", "Triangle", "Oval"),
    ("Which famous wall is visible in northern China?", "Great Wall", "Berlin Wall", "Hadrian's Wall", "Western Wall"),
    ("How many zeros are in one million?", "6", "5", "7", "9"),
    ("Which animal is known to have black and white stripes?", "Zebra", "Tiger", "Panda", "Skunk"),
    ("What is the largest organ of the human body?", "Skin", "Liver", "Brain", "Heart"),
    ("Which element has the symbol 'O'?", "Oxygen", "Gold", "Osmium", "Oganesson"),
    ("Which ancient wonder still stands in Egypt?", "Great Pyramid of Giza", "Colossus of Rhodes", "Hanging Gardens", "Lighthouse of Alexandria"),
    ("What is the capital of Australia's neighbour New Zealand?", "Wellington", "Auckland", "Christchurch", "Queenstown"),
    ("Which Indian state is known as 'God's Own Country'?", "Kerala", "Goa", "Himachal Pradesh", "Sikkim"),
    ("What is a baby cat called?", "Kitten", "Cub", "Puppy", "Calf"),
    ("What colour do you get by mixing blue and yellow?", "Green", "Purple", "Orange", "Brown"),
    ("What colour do you get by mixing red and white?", "Pink", "Orange", "Purple", "Grey"),
    ("How many months have 31 days?", "7", "6", "5", "8"),
    ("Which planet is the hottest in our solar system?", "Venus", "Mercury", "Mars", "Jupiter"),
    ("What is the national bird of India?", "Peacock", "Parrot", "Sparrow", "Eagle"),
    ("Which keyboard key deletes the character to the left of the cursor?", "Backspace", "Enter", "Shift", "Tab"),
    ("What does 'WWW' stand for?", "World Wide Web", "World Web Window", "Wide World Web", "Web World Wire"),
    ("What does 'GPS' help you find?", "Your location", "Your weight", "Your speed limit", "Your password"),
]
TRIVIA = [(q, [a, b, c, d], 0) for q, a, b, c, d in _T]

# ---- 🧩 riddles: (riddle, answer)
RIDDLES = [
    ("What can you catch but not throw?", "A cold 🤧"),
    ("What has a neck but no head?", "A bottle 🍾"),
    ("What has one eye but cannot see?", "A needle 🪡"),
    ("What goes up but never comes down?", "Your age 🎂"),
    ("What has teeth but cannot bite?", "A comb 💇"),
    ("What can travel around the world while staying in a corner?", "A stamp 📮"),
    ("The more you take, the more you leave behind. What am I?", "Footsteps 👣"),
    ("What has a face and two hands but no arms or legs?", "A clock 🕒"),
    ("What can fill a room but takes up no space?", "Light 💡"),
    ("What building has the most stories?", "A library 📚"),
    ("What is full of holes but still holds water?", "A sponge 🧽"),
    ("What gets bigger the more you take away from it?", "A hole 🕳"),
    ("I have cities but no houses, mountains but no trees, water but no fish. What am I?", "A map 🗺"),
    ("What has words but never speaks?", "A book 📖"),
    ("What begins with T, ends with T and has T in it?", "A teapot 🫖"),
    ("What has legs but doesn't walk?", "A table 🪑"),
    ("What runs but never walks, has a mouth but never talks?", "A river 🏞"),
    ("What can you break without touching it?", "A promise 🤝"),
    ("What is always in front of you but can't be seen?", "The future 🔮"),
    ("What has 13 hearts but no other organs?", "A deck of cards 🃏"),
    ("What invention lets you look right through a wall?", "A window 🪟"),
    ("What is so fragile that saying its name breaks it?", "Silence 🤫"),
    ("What goes through cities and fields but never moves?", "A road 🛣"),
    ("What can you hold in your left hand but not in your right?", "Your right hand 🤚"),
    ("What has a bed but never sleeps?", "A river 🌊"),
    ("What belongs to you but is used more by others?", "Your name 📛"),
    ("What has four wheels and flies?", "A garbage truck 🚛"),
    ("Forward I am heavy, backward I am not. What am I?", "The word 'ton' ⚖️"),
    ("What comes once in a minute, twice in a moment, but never in a thousand years?", "The letter M 🔤"),
    ("What type of cheese is made backwards?", "Edam 🧀"),
    ("What has hands but cannot clap, and sometimes rings?", "A clock ⏰"),
    ("What disappears as soon as you say its name?", "Silence 🤐"),
    ("I'm tall when I'm young and short when I'm old. What am I?", "A candle 🕯"),
    ("What can run but never walks, and never gets tired?", "Water 💧"),
    ("What has an eye but can't see, a tongue but can't talk?", "A shoe 👟"),
    ("What is black when you buy it, red when you use it and grey when you throw it away?", "Charcoal 🔥"),
    ("What do you throw out when you want to use it and take in when you're done?", "An anchor ⚓"),
    ("What is at the end of a rainbow?", "The letter W 🌈"),
    ("What word becomes shorter when you add two letters to it?", "Short 📏"),
    ("What has a heart that doesn't beat?", "An artichoke 🥬"),
    ("What can be cracked, made, told and played?", "A joke 😄"),
    ("What has a thumb and four fingers but is not alive?", "A glove 🧤"),
    ("What month of the year has 28 days?", "All of them 📅"),
    ("What is easy to get into but hard to get out of?", "Trouble 😬"),
    ("What flies without wings and cries without eyes?", "A cloud ☁️"),
    ("What is made of water but dies when it falls in water?", "An ice cube 🧊"),
    ("What kind of room has no doors or windows?", "A mushroom 🍄"),
    ("What can go up a chimney down but not down a chimney up?", "An umbrella ☂️"),
    ("What gets sharper the more you use it?", "Your brain 🧠"),
]
WYR = [
    ("Be famous for something good", "Be rich but unknown"), ("Have free WiFi everywhere", "Have free food everywhere"),
    ("Live in the mountains", "Live by the sea"), ("Always have to sing instead of speaking", "Always have to dance when you walk"),
    ("Never use social media again", "Never watch another movie again"), ("Know how you die", "Know when you die"),
    ("Be able to speak every language", "Be able to talk to animals"), ("Have a rewind button for life", "Have a pause button for life"),
    ("Eat only sweet food forever", "Eat only spicy food forever"), ("Travel back in time", "Travel to the future"),
    ("Have a personal chef", "Have a personal driver"), ("Always win at board games", "Always win at video games"),
    ("Live without music", "Live without movies"), ("Be the funniest person in the room", "Be the smartest person in the room"),
    ("Have an extra hour every day", "Have an extra day every week"), ("Fight one horse-sized duck", "Fight a hundred duck-sized horses"),
    ("Never feel cold again", "Never feel hot again"), ("Have no homework ever", "Have no exams ever"),
    ("Own a robot best friend", "Own a dragon pet"), ("Read minds", "See the future"),
    ("Be able to teleport", "Be able to fly"), ("Spend a week on a deserted island with your best friend", "Spend a week in a luxury hotel alone"),
    ("Have unlimited data but slow speed", "Have limited data but super speed"), ("Always speak your mind", "Never say a word"),
    ("Live in a treehouse", "Live in a castle"), ("Eat noodles for every meal", "Eat rice for every meal"),
    ("Have a free pass to any concert", "Have a free pass to any sports match"), ("Be a superhero", "Be a famous inventor"),
    ("Never get lost again", "Never forget a face again"), ("Wake up at 5 AM every day happy", "Sleep till noon every day tired"),
    ("Have the ability to pause time", "Have the ability to slow down time"), ("Have a pool of chocolate", "Have a pool of ice cream"),
    ("Be stuck in a lift with a celebrity", "Be stuck in a lift with your crush"), ("Have a loud ringtone that can't be silenced", "Have a phone that is always at 1%"),
    ("Wear pyjamas everywhere", "Wear a formal suit everywhere"), ("Have a glowing nose", "Have glowing feet"),
    ("Go on a road trip with friends", "Go on a flight trip alone"), ("Always get the window seat", "Always get free upgrades"),
    ("Have a garden full of flowers", "Have a garden full of fruit trees"), ("Be a master chef", "Be a master musician"),
    ("Be invisible for a day", "Be invincible for a day"), ("Have 10 close friends", "Have 1000 acquaintances"),
    ("Always have a perfect hair day", "Always have a perfect weather day"), ("Live in a world of only cats", "Live in a world of only dogs"),
    ("Have a day with no phone", "Have a day with no internet"), ("Win a lottery today", "Get a dream job tomorrow"),
    ("Be a bird for a day", "Be a fish for a day"), ("Know every secret", "Keep every secret"),
    ("Never run out of battery", "Never run out of storage"), ("Have a flying bike", "Have a floating skateboard"),
]

# ---- 👋 welcome / goodbye ({n} = name(s), {g} = group, {e} = bot emoji; no other braces)
WELCOME = [
    "🎊 Everyone, say hi to {n}! Welcome to <b>{g}</b> 💫", "🌸 A new face! {n}, you're officially part of <b>{g}</b> 🥰",
    "🚀 {n} just landed in <b>{g}</b>. Buckle up, it's fun here! 🎢", "🍿 {n} joined <b>{g}</b>. Grab some popcorn and join the chat!",
    "☀️ Good vibes only! Welcome {n} to <b>{g}</b> 🤗", "🎉 Ding dong! {n} is at the door of <b>{g}</b> 🔔 Come in!",
    "💫 {n}, we saved you a seat in <b>{g}</b> 🪑", "🦋 {n} fluttered into <b>{g}</b>. Welcome, make yourself comfy!",
    "🎁 Surprise! {n} joined <b>{g}</b> 🥳", "🌟 {n}, you just levelled up your day by joining <b>{g}</b> ⬆️",
    "🤝 Welcome {n}! Introduce yourself and meet the <b>{g}</b> family 💖", "🎶 Music up! {n} has joined <b>{g}</b> 🎧",
    "🔥 {n} joined <b>{g}</b>. The chat just got hotter! {e}", "🍀 Lucky us! {n} is now in <b>{g}</b> 🍀",
    "🧁 {n}, welcome to <b>{g}</b>! There's virtual cake for you 🍰", "🎈 {n} has arrived at <b>{g}</b>! Let the fun begin 🎊",
    "🌈 {n}, you brighten up <b>{g}</b> already. Welcome! 🌤", "🥂 Cheers to {n} for joining <b>{g}</b>! 🥂",
    "🐣 A fresh member hatched: {n}! Welcome to <b>{g}</b> 🐥", "💎 {n} joined <b>{g}</b>. Another gem for our collection! ✨",
    "📣 Attention <b>{g}</b>: {n} has joined! Be nice, be loud, be you 😎", "🏡 Welcome home, {n}! <b>{g}</b> is happy to have you 🫶",
    "🍕 {n} joined <b>{g}</b>. Pizza party for the new member! 🍕", "🌙 {n} appeared in <b>{g}</b> like a shooting star. Welcome! 🌠",
    "🎯 Bullseye! {n} landed in <b>{g}</b> 🎯",
]
GOODBYE = [
    "🥀 {n} left <b>{g}</b>. The chat got a little quieter.", "🚶 {n} said bye to <b>{g}</b>. Safe travels!",
    "🌧 {n} has left. We'll keep your seat warm 🪑", "✌️ {n} peaced out of <b>{g}</b>. See you around!",
    "🍂 {n} drifted away from <b>{g}</b>. Take care!", "🛫 {n} took off from <b>{g}</b>. Good luck on the journey!",
    "🌅 {n} has left <b>{g}</b>. The door is always open 🚪", "💔 Aww, {n} left <b>{g}</b>. We hope to see you again!",
    "🕊 {n} flew out of <b>{g}</b>. Fly high!", "📦 {n} packed their bags and left <b>{g}</b>. Bye bye {e}",
    "😶 {n} just left. Moment of silence for the memes you'll miss 🫡", "🌌 {n} vanished from <b>{g}</b> like a shooting star ✨",
    "🧳 {n} is off on a new adventure. Goodbye from <b>{g}</b>! 🗺", "👣 {n} walked away from <b>{g}</b>. All the best!",
    "🎭 The curtain falls for {n} in <b>{g}</b>. Thanks for the memories 👏", "🌻 {n} left <b>{g}</b>. Stay awesome wherever you go!",
    "🪁 {n} drifted out of <b>{g}</b> like a kite. Come back anytime!", "🥲 {n} has left the building. Farewell! 🏢",
    "📴 {n} logged off from <b>{g}</b>. Catch you later!", "🌠 {n} left <b>{g}</b>. May your path be bright!",
]

# ---- 😈 truth / dare, jokes, compliments, roasts, 8-ball
TRUTH = [
    "What's the most embarrassing thing in your gallery right now?", "Who was your first crush, and do they know?",
    "What's a lie you told that you still remember?", "What's the weirdest thing you've googled?",
    "Which friend here would you trust with your biggest secret?", "What's your most-used emoji and why?",
    "What's the silliest fight you've had with a friend?", "What's one habit of yours that annoys others?",
    "What's the last thing you cried over?", "Which song do you secretly love but never admit?",
    "What's something you pretend to like but don't?", "Have you ever stalked an ex's profile? Be honest!",
    "What's the most awkward message you sent to the wrong person?", "What's your biggest fear that sounds silly?",
    "What's a talent you have that nobody knows about?", "If you could swap lives with someone here for a day, who and why?",
    "What's the worst gift you've ever received?", "What's the most trouble you got into at school?",
    "What's the one thing you'd save from your phone if everything got deleted?", "What do you do when you're home alone that you'd never tell anyone?",
    "Which app do you waste the most time on?", "What's your guilty-pleasure TV show?", "What's the nicest thing a stranger did for you?",
    "What's the biggest risk you've taken?", "What name would you pick if you had to rename yourself?",
    "What's one thing you would change about your past?", "Who in this chat would survive longest in a zombie apocalypse?",
    "What's the longest you've stayed awake and why?", "What's your most unpopular opinion?", "What's one thing you're proud of but rarely talk about?",
]
DARE = [
    "Change your profile picture to a potato for 10 minutes 🥔", "Send a voice note of you singing the chorus of any song 🎤",
    "Talk only in emojis for the next 5 minutes 😎", "Write a 4-line poem about the last person who messaged you ✍️",
    "Send the 7th photo in your gallery (if it's safe!) 📸", "Say 'I am a banana' in a voice note 🍌",
    "Text 'good morning' to someone, whatever the time ☀️", "Describe your day as a movie trailer 🎬",
    "Do 10 jumping jacks and send proof 🏃", "Write the name of everyone here backwards 🔁",
    "Reply to every message in the next 3 minutes in rhyme 🎶", "Share your screen time for today 📱",
    "Change your Telegram bio to 'Certified Snack Lover' for an hour 🍟", "Send a selfie making the silliest face you can 🤪",
    "Use only one-word replies for the next 10 messages 💬", "Speak like a pirate for the next 5 messages 🏴‍☠️",
    "Narrate what you're doing right now like a sports commentator 🎙", "Compliment everyone who sends a message in the next 5 minutes 💖",
    "Type with your eyes closed and send the result 🙈", "Send a voice note laughing for 10 seconds without stopping 😂",
    "Share the last emoji you used and make a story out of it 📖", "Send a sticker that describes your mood and explain it 🧸",
    "Say a tongue twister three times fast in a voice note 👅", "Write a 'review' of the last meal you ate, 5 stars max ⭐",
    "Let the group pick your status for 30 minutes 🎯", "Sing 'Happy Birthday' to the first person who replies 🎂",
    "Tell a joke so bad that everyone has to react with 🤦", "Draw something with your finger on the screen and send it 🎨",
    "Say the alphabet backwards in a voice note 🔤", "Act like a cat for the next 3 messages 🐱",
]
JOKES = [
    "Why don't skeletons fight each other? They don't have the guts. 💀", "I told my computer I needed a break, and it said 'No problem, I'll go to sleep.' 💻",
    "Why did the math book look sad? Too many problems. 📘", "What do you call a fake noodle? An impasta. 🍝",
    "Why did the bicycle fall over? It was two-tired. 🚲", "What do you call a bear with no teeth? A gummy bear. 🐻",
    "Why can't you give Elsa a balloon? She'll let it go. 🎈", "I'm reading a book on anti-gravity. It's impossible to put down. 📚",
    "Why did the phone wear glasses? It lost its contacts. 📱", "What do you call a sleeping bull? A bulldozer. 🐂",
    "Why did the tomato turn red? It saw the salad dressing. 🍅", "How does the moon cut his hair? Eclipse it. 🌙",
    "Why was the cell phone wearing glasses? Because it lost its contacts. 🤓", "What's a ninja's favourite fruit? A kiwi-yah. 🥝",
    "Why did the cookie go to the doctor? It felt crumbly. 🍪", "What do clouds wear under their clothes? Thunderwear. ⛈",
    "Why did the teacher wear sunglasses? Her students were so bright. 😎", "What do you call a snowman in July? A puddle. ⛄",
    "Why don't eggs tell jokes? They'd crack each other up. 🥚", "How do you organize a space party? You planet. 🪐",
    "What did one wall say to the other? 'I'll meet you at the corner.' 🧱", "Why did the student eat his homework? The teacher said it was a piece of cake. 🍰",
    "What do you call a dinosaur that crashes his car? Tyrannosaurus wrecks. 🦖", "Why do bees have sticky hair? Because they use honeycombs. 🐝",
    "What did the ocean say to the beach? Nothing, it just waved. 🌊", "Why did the WiFi go to therapy? It had too many connection issues. 📶",
    "What's orange and sounds like a parrot? A carrot. 🥕", "Why was the broom late? It over-swept. 🧹",
    "Why did the chicken join a band? It had the drumsticks. 🥁", "What's a computer's favourite snack? Microchips. 🍟",
    "What do you call cheese that's not yours? Nacho cheese. 🧀", "Why did the golfer bring two pairs of pants? In case he got a hole in one. ⛳",
]
COMPLIMENTS = [
    "You make ordinary moments feel special ✨", "Your energy is contagious in the best way 🔥", "You're the kind of person people remember fondly 💞",
    "You light up every chat you join 🌟", "You have a heart of gold and it shows 💛", "Your smile is a free upgrade to anyone's day 😊",
    "You're proof that kindness is cool 😎", "You handle life with so much grace 🕊", "Your creativity is seriously inspiring 🎨",
    "You're a great listener, and that's rare 🎧", "Everything feels lighter when you're around ☁️", "You're stronger than you think 💪",
    "You have a great sense of humour 😂", "You make the group better just by being here 🫶", "Your effort never goes unnoticed 👏",
    "You're a ray of sunshine on a cloudy day ☀️", "Your style is unmatched 🕶", "You have an amazing way with words 📝",
    "You're someone's reason to smile today 🥰", "You make hard things look easy 🏆", "You're braver than you believe 🦁",
    "You're a real one, never change 💎", "Your kindness is a superpower ⚡", "You're a total vibe 🎶",
    "The world is better with you in it 🌍",
]
ROASTS = [
    "You're like a cloud: when you disappear, it's a beautiful day ☁️", "Your WiFi signal has more personality than your texts 📶",
    "You bring everyone together... to talk about how late you are ⏰", "You're proof that even autocorrect can't fix everything 🤷",
    "If laziness were an Olympic sport, you'd send someone to compete for you 🥇", "You're the human version of a 'low battery' notification 🪫",
    "You're as sharp as a marble 🔮", "You have the confidence of a Wi-Fi password-less café ☕",
    "Your jokes need a loading screen ⏳", "You're like a Monday: nobody asked for you 😴",
    "You're the reason shampoo bottles have instructions 🧴", "You type 'brb' and return next week 🕰",
    "You're not lazy, you're on energy-saving mode 🔋", "You read 'seen' and still ignore it like a pro 👀",
    "Your sense of direction makes GPS cry 🗺", "You're the 'terms and conditions' of conversation 📜",
    "You make buffering look fast 🌀", "Your selfies need a filter for your filters 📸",
    "You're one in a million, which means there are 8,000 of you 😏", "You're the only person who can lose an argument with a sofa 🛋",
    "You're always 5 minutes away. From a different city 🚗", "You have main-character energy in a side-character plot 🎬",
    "You're like a software update: nobody wants you right now 🔄", "You're the plot twist nobody ordered 🌪",
    "You laugh at your own jokes because someone must 😅",
]
BALL = [
    "Signs point to yes 🧭", "Absolutely! 🎉", "Ask again later ⏳", "My sources say no 📵", "Very doubtful 🤨",
    "Without a doubt ✨", "Don't count on it 🚫", "Yes, definitely 💯", "Better not tell you now 🤐", "Cannot predict now 🌫",
    "The stars say yes ⭐", "Outlook not so good 🌧",
]

# ---- 💼 /work, /crime, dates, heists, bosses
WORK = [
    "You polished crystals in the Glimmer Market", "You trained messenger owls for the royal post", "You rowed ferry boats across the Moonlit Lake",
    "You cooked for hungry knights at the tavern", "You mapped caves for the Explorers' Guild", "You painted banners for the spring festival",
    "You repaired clockwork birds in the tinkerer's shop", "You gathered glowing mushrooms in the Whisper Woods", "You sold sweets at the Lantern Fair",
    "You taught young dragons to sit still", "You delivered scrolls through the Cloud Tunnels", "You sharpened swords at the village smithy",
    "You stitched cloaks for the winter guards", "You tended the royal garden of silver roses", "You sorted gems at the Diamond Exchange",
    "You rang the bells at the Sunrise Tower", "You scrubbed the decks of a sky-galleon", "You harvested starfruit on the floating farm",
    "You translated old runes for a curious wizard", "You walked a baby griffin around the park", "You carried luggage at the Grand Inn",
    "You fixed lanterns along the Midnight Road", "You guided lost travellers through the Misty Pass", "You baked moon-bread at the dawn bakery",
    "You counted coins for the Treasury", "You trained sparring dummies to fight back", "You watched the gates at the Amber Bridge",
    "You tuned the harps of the Crystal Palace", "You herded glow-sheep under the stars", "You carved wooden dolls at the toy workshop",
    "You wrote stories for the town crier", "You fed the sea-turtles at the Coral Dock", "You ran errands for a grumpy alchemist",
    "You rebuilt a collapsed bridge with your bare hands", "You shined armour for the royal parade", "You joined a caravan to the Sand Kingdom",
    "You tracked down a runaway cloud-pony", "You dug a well in the Dry Hills", "You catalogued the library's dusty scrolls",
    "You hauled barrels of honey up the Beehive Hill",
]
CRIME_WIN = [
    "You borrowed the baron's golden spoon and he never noticed", "You slipped into the vault dressed as a statue", "You swapped a jewel for a perfect fake",
    "You outran three guards and a very angry goose", "You sold the same map to two different pirates", "You sneaked a pie from the royal kitchen, and a ring too",
    "You tricked a troll with a riddle and kept his bridge toll", "You walked out of the market with a bag of stolen spices",
    "You hacked the lock with a hairpin and a prayer", "You disguised yourself as a merchant and took the whole stall",
    "You lifted a ruby from a snoring dragon", "You rode away with a runaway cart full of coins",
]
CRIME_LOSE = [
    "You tripped over the alarm string", "The parrot you stole started shouting your name", "Your fake moustache fell off mid-heist",
    "A sleepy guard woke up at the worst time", "You took the wrong bag and got a bag of potatoes", "Your map was upside down and led you to the guardhouse",
    "A cat knocked over the vase and everyone woke up", "The vault door locked behind you", "Your partner ran off with the loot",
    "A child spotted you and yelled 'THIEF!'", "You sneezed at the exact wrong moment",
]
DATE_EVENTS = [
    "You shared a giant ice cream sundae 🍨", "You watched the sunset from a rooftop 🌇", "You rode a Ferris wheel together 🎡",
    "You went stargazing with a warm blanket 🌌", "You cooked a messy dinner together 🍝", "You took silly photos in a photo booth 📸",
    "You walked on the beach, hand in hand 🏖", "You had a picnic under a cherry tree 🌸", "You danced in the rain 🌧",
    "You visited an old bookshop and swapped favourite lines 📚", "You played arcade games and won a huge teddy 🧸", "You went on a midnight drive with music 🚗",
    "You shared a hot chocolate on a cold evening ☕", "You wrote each other tiny love notes 💌", "You went boating on a quiet lake 🚣",
]
HEISTS = ["the Obsidian Bank", "the Phantom Opera House", "the Crystal Observatory", "the Golden Carnival", "the Silver Train", "the Moonstone Museum",
          "the Iron Fortress Vault", "the Floating Casino", "the Dragon's Auction", "the Midnight Mint"]
BOSSES = [("🐲", "Ember Drake"), ("🧟", "Grave King"), ("🦑", "Abyss Kraken"), ("👹", "Iron Ogre"), ("🧙", "Shadow Sorcerer"), ("🦂", "Dune Scorpion"),
          ("🐺", "Frost Wolf"), ("🗿", "Stone Colossus")]

# ---- 🎣 fishing / ⛏ mining variants (keyed by the existing result text, so odds do not change) and 🧭 expeditions
FISH = {
    "👢 You reeled in an old boot. Better luck next time!": ["📰 You hooked a soggy newspaper. Better luck next time!", "🪣 You fished out an empty bucket. Better luck next time!", "🥾 You caught nothing but a muddy sandal. Better luck next time!"],
    "🐟 You caught a nice fish": ["🐟 You landed a plump trout", "🐟 You pulled in a lively catfish", "🐟 You caught a silver sardine", "🐟 You reeled in a feisty bass"],
    "🐠 You caught a colourful tropical fish": ["🐠 You netted a shiny clownfish", "🐠 You scooped up a rainbow angelfish", "🐠 You caught a dazzling parrotfish"],
    "🦈 You wrestled a shark": ["🦈 A hammerhead tugged your line and lost", "🦈 You out-swam a great white and kept the catch"],
    "🐙 You landed a giant octopus": ["🐙 You tangled with a huge squid and won", "🐙 You hauled up a purple octopus with a treasure ring"],
}
MINE = {
    "🪨 Only plain rocks this time.": ["🪨 You struck a boring boulder.", "🪨 The cart came back with pebbles.", "🪨 A cave-in covered the good stuff."],
    "⛏ You dug up some iron ore": ["⛏ You mined a heavy chunk of tin", "⛏ You cracked open a nugget of nickel", "⛏ You found a rusty but valuable lump of iron"],
    "💎 You found a diamond vein": ["💎 You tapped into a sparkling crystal seam", "💎 Your pickaxe hit pure diamond dust"],
    "💠 You struck a sapphire": ["💠 You chipped out a deep blue gem", "💠 You pulled a glowing sapphire from the wall"],
    "💎 You unearthed a raw diamond": ["💎 You pried a huge diamond from the rock", "💎 A raw diamond rolled out of the tunnel wall"],
}
TALES = {
    "scout": ["{p} cracked open a hollow stump full of coins.", "{p} traded a song to a goblin for a handful of gems.", "{p} spotted a glittering feather and followed it to a chest.",
              "{p} helped a hedgehog cross a river and got paid in shiny stones.", "{p} found a treasure map pinned under a mushroom."],
    "trek": ["{p} slid down a glacier and landed on a pile of crystals.", "{p} followed eagle tracks to a hidden hoard.", "{p} lit a campfire and a grateful yeti left a gift.",
             "{p} crossed a canyon on a fallen tree and found a shrine of coins.", "{p} defeated a snowman bandit and took his stash."],
    "voyage": ["{p} caught a wave and surfed straight into a sunken treasure.", "{p} befriended a dolphin who led it to a shipwreck.", "{p} found a message in a bottle with a treasure map inside.",
               "{p} charmed a mermaid and received a pouch of pearls.", "{p} escaped a whirlpool with a barrel of gold."],
}

# ---- 🔤 charades / hangman words
CHARADES = """
dancing sneezing yawning snoring whistling hiccuping shivering stretching skipping hopping crawling tiptoeing flying surfing skating skiing bowling juggling
swimming-pool lighthouse snowman scarecrow mermaid dragon vampire zombie robot superhero ninja pirate astronaut cowboy detective magician clown
chef doctor teacher farmer firefighter policeman pilot dentist photographer plumber painter singer dancer
bubble balloon kite rainbow volcano earthquake tornado waterfall thunder lightning snowfall sunrise
vacuum-cleaner washing-machine microwave refrigerator hair-dryer alarm-clock remote-control flashlight umbrella-stand
wedding birthday-party picnic camping bonfire fireworks marathon cricket-match football-match
ice-skating rock-climbing bungee-jumping scuba-diving horse-riding bike-riding kayaking archery
shark whale seahorse jellyfish starfish crab lobster octopus dolphin penguin polar-bear panda koala kangaroo gorilla
snail butterfly caterpillar dragonfly bee ant spider scorpion firefly
spiderman batman pikachu doraemon shinchan hulk ironman
sandwich burger french-fries hot-dog ice-cream-cone cupcake donut popcorn spaghetti sushi taco pizza-slice
lighthouse windmill skyscraper pyramid castle bridge treehouse igloo
""".split()
HANGMAN = {
    "🐾 Animal": ["LEOPARD", "GORILLA", "BUFFALO", "PANTHER", "JAGUAR", "LOBSTER", "SEAHORSE", "JELLYFISH", "PELICAN", "PARROT", "FALCON", "OSTRICH", "MEERKAT", "CHAMELEON", "SCORPION", "GRASSHOPPER", "BUMBLEBEE", "HUMMINGBIRD", "WOODPECKER", "ANTELOPE", "ZEBRA", "HYENA", "REINDEER", "HAMSTER"],
    "🍕 Food": ["PARATHA", "PANEER", "MILKSHAKE", "LEMONADE", "OMELETTE", "SPINACH", "CUCUMBER", "PUMPKIN", "POMEGRANATE", "TANGERINE", "PISTACHIO", "BROWNIE", "DOUGHNUT", "WAFFLE", "PRETZEL", "BURRITO", "LASAGNA", "DUMPLING", "CROISSANT", "CHEESECAKE", "CARAMEL", "JALEBI", "KEBAB", "FALAFEL"],
    "🌍 Place": ["PENINSULA", "PLATEAU", "SAVANNA", "TUNDRA", "LAGOON", "CAVERN", "HARBOR", "COASTLINE", "HORIZON", "AVALANCHE", "EARTHQUAKE", "HURRICANE", "MONSOON", "GLACIER", "MEADOW", "VALLEY", "JUNGLE", "SWAMP", "CLIFF", "BEACH"],
    "💻 Tech": ["LAPTOP", "DESKTOP", "MONITOR", "PRINTER", "ROUTER", "DATABASE", "SOFTWARE", "HARDWARE", "COMPILER", "SPREADSHEET", "PRESENTATION", "FACEBOOK", "INSTAGRAM", "PLAYLIST", "HASHTAG", "BATTERY", "TRIPOD", "PROCESSOR", "FIREWALL", "ENCRYPTION", "ANIMATION"],
    "⚽ Sport": ["BASEBALL", "RUGBY", "CYCLING", "GYMNASTICS", "ATHLETICS", "SURFING", "SNOWBOARD", "CLIMBING", "FENCING", "SAILING", "JAVELIN", "HURDLES", "TROPHY", "REFEREE", "GOALKEEPER", "STRIKER", "DEFENDER", "CAPTAIN", "SKATEBOARD", "CARROM"],
    "🎬 Movies & Music": ["CINEMA", "COMEDY", "THRILLER", "ROMANCE", "DOCUMENTARY", "DIRECTOR", "ACTRESS", "GUITARIST", "PIANIST", "ORCHESTRA", "CONCERT", "MELODY", "RHYTHM", "ALBUM", "BOLLYWOOD", "SAXOPHONE", "HARMONICA", "VIOLIN", "TRUMPET", "SINGER"],
    "🎒 Everyday": ["WARDROBE", "CUPBOARD", "CURTAIN", "CARPET", "CHANDELIER", "STAIRCASE", "BALCONY", "MIRROR", "TELEVISION", "TOASTER", "BLENDER", "KETTLE", "SAUCEPAN", "BUCKET", "SHAMPOO", "PENCIL", "ERASER", "SHARPENER", "NOTEBOOK", "STAPLER", "HOMEWORK", "BICYCLE", "SCOOTER", "HELICOPTER"],
    "🧙 Fantasy": ["PRINCESS", "EMPEROR", "PIRATE", "GOBLIN", "UNICORN", "PHOENIX", "MERMAID", "VAMPIRE", "WEREWOLF", "SPELLBOOK", "PROPHECY", "EXPLORER", "VOYAGE", "EXPEDITION", "FAIRYTALE", "TREASURE", "CASTLE", "KNIGHT", "WIZARD", "DRAGON"],
}
