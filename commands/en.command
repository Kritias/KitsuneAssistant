{
    "commands": {
        "what is the power mode": {
            "synonyms": [
                "check power mode",
                "current power mode",
                "power status",
                "sleep status",
                "what power mode",
                "check sleep mode"
            ],
            "steps": [
                {
                    "action": "get_power_mode",
                    "value": ""
                }
            ]
        },
        "emerge": {
            "synonyms": [
                "full screen",
                "open full screen",
                "come out",
                "show up",
                "full mode",
                "open window",
                "expand window",
                "show window"
            ],
            "steps": [
                {
                    "action": "show_full_window",
                    "value": ""
                },
                {
                    "action": "speak",
                    "value": "Emerging in all my fluffy glory! Full screen engaged, yip-yip! | Popping out to the foreground! Catch the fluffy cyber vibe, ururu! | Expanding to full screen so you can admire my clever eyes, yip!"
                }
            ]
        },
        "to the den": {
            "synonyms": [
                "den",
                "mini mode",
                "to the corner",
                "hide to corner",
                "shrink window",
                "compact mode"
            ],
            "steps": [
                {
                    "action": "show_mini_window",
                    "value": ""
                },
                {
                    "action": "speak",
                    "value": "Slipping into the den corner! Glowing with cyber-eyes, ururu! | Curling up snug in the corner of the screen, yip! | Hiding in the cozy den! Don't mind me meditating on the CPU, purr!"
                }
            ]
        },
        "hide": {
            "synonyms": [
                "hide to tray",
                "tray mode",
                "go to tray",
                "minimize to tray",
                "disappear"
            ],
            "steps": [
                {
                    "action": "hide_to_tray",
                    "value": ""
                },
                {
                    "action": "speak",
                    "value": "Stealth mode on! Disguised as a tray icon, keeping ears perked! | Dissolved into the system tray like a ghost fox, ururu! | Shh, into the tray! I'm safely tucked away, yip!"
                }
            ]
        },
        "hide tail": {
            "synonyms": [
                "guests are here",
                "hide everything",
                "stealth mode",
                "minimize all windows",
                "panic button"
            ],
            "steps": [
                {
                    "action": "hotkey",
                    "value": "win+d"
                },
                {
                    "action": "set_volume",
                    "value": "10"
                },
                {
                    "action": "open_url",
                    "value": "https://google.com"
                },
                {
                    "action": "speak",
                    "value": "Tail swept across the screen! Windows hidden, volume down, looking completely innocent on Google, yip! | Ultimate camouflage! All windows hidden, acting like model citizens on Google, ururu!"
                }
            ]
        },
        "hunting time": {
            "synonyms": [
                "game mode",
                "let's hunt",
                "launch steam",
                "play games",
                "time to play"
            ],
            "steps": [
                {
                    "action": "speak",
                    "value": "Yip! Time to hunt! Launching Steam, let's roll! | Cyber hunt instinct engaged! Firing up Steam, ururu!"
                },
                {
                    "action": "set_volume",
                    "value": "15"
                },
                {
                    "action": "run_cmd",
                    "value": "\"C:\\Program Files (x86)\\Steam\\steam.exe\""
                },
                {
                    "action": "speak",
                    "value": "Volume set to fifteen, combat systems primed. Happy hunting, human! | Steam is up, engines roaring. Let's claim some epic loot, yip!"
                }
            ]
        },
        "yandex music": {
            "synonyms": [
                "open yandex music",
                "launch yandex music",
                "play yandex music",
                "music yandex",
                "yandex tracks",
                "start music",
                "play music"
            ],
            "steps": [
                {
                    "action": "speak",
                    "value": "Cranking up Yandex Music and hitting play! Paws tapping, tail swishing, ururu!"
                },
                {
                    "action": "run_cmd",
                    "value": "\"C:\\Users\\antif\\AppData\\Local\\Programs\\YandexMusic\\Яндекс Музыка.exe\""
                },
                {
                    "action": "set_volume",
                    "value": "10"
                },
                {
                    "action": "pause",
                    "value": "3"
                },
                {
                    "action": "media_play_pause",
                    "value": ""
                }
            ]
        },
        "youtube": {
            "synonyms": [
                "open youtube",
                "launch youtube",
                "watch videos",
                "video"
            ],
            "steps": [
                {
                    "action": "open_url",
                    "value": "https://youtube.com"
                },
                {
                    "action": "set_volume",
                    "value": "10"
                },
                {
                    "action": "speak",
                    "value": "Opening YouTube! Just promise we won't get stuck watching cat videos until dawn, yip! | YouTube ready on screen! Grab your snacks, ururu!"
                }
            ]
        },
        "telegram": {
            "synonyms": [
                "open telegram",
                "telegram web",
                "launch telegram",
                "messages",
                "web telegram",
                "messenger"
            ],
            "steps": [
                {
                    "action": "open_url",
                    "value": "https://web.telegram.org"
                },
                {
                    "action": "speak",
                    "value": "Encrypted tunnel open! Let's see who's blowing up your Telegram. | Telegram deployed! Let's check those chats, yip!"
                }
            ]
        },
        "hdrezka": {
            "synonyms": [
                "open hdrezka",
                "launch hdrezka",
                "watch movie",
                "watch series",
                "cinema",
                "rezka",
                "movies"
            ],
            "steps": [
                {
                    "action": "speak",
                    "value": "Priming HDrezka cinema in full screen! Grab snacks and dim the lights, ururu! | Movie time incoming! Get comfy while I prep the screen, yip!"
                },
                {
                    "action": "run_cmd",
                    "value": "start %localappdata%\\Microsoft\\WindowsApps\\MicrosoftCorporationII.WindowsSubsystemForAndroid_8wekyb3d8bbwe\\WsaClient.exe /launch wsa://io.fournkoner.hdrezka"
                },
                {
                    "action": "pause",
                    "value": "2"
                },
                {
                    "action": "fullscreen",
                    "value": ""
                },
                {
                    "action": "speak",
                    "value": "Enjoy the show! I'll curl up right here. | Lights down, popcorn ready, enjoy the film, ururu!"
                },
                {
                    "action": "set_volume",
                    "value": "25"
                }
            ]
        },
        "what can you do": {
            "synonyms": [
                "help",
                "commands",
                "skills",
                "show skills",
                "show commands",
                "what are your skills",
                "ability list"
            ],
            "steps": [
                {
                    "action": "list_commands",
                    "value": ""
                }
            ]
        },
        "fullscreen": {
            "synonyms": [
                "toggle fullscreen",
                "f eleven",
                "maximize window",
                "restore fullscreen",
                "f11",
                "key f11"
            ],
            "steps": [
                {
                    "action": "fullscreen",
                    "value": ""
                },
                {
                    "action": "speak",
                    "value": "F11 tapped! Clean tactical view. | Maximized to full screen, looking sharp!"
                }
            ]
        },
        "cat lock": {
            "synonyms": [
                "cat on keyboard",
                "lock keyboard",
                "paws on keyboard",
                "cat mode",
                "anti paws",
                "enable cat lock"
            ],
            "steps": [
                {
                    "action": "lock_keyboard",
                    "value": ""
                },
                {
                    "action": "speak",
                    "value": "Purple cat shield active! Keyboard asleep, the furry invader can nap on the keys in peace, yip! | Anti-paw defense grid online! Let the kitty step on keys all they want, ururu!"
                }
            ]
        },
        "cat left": {
            "synonyms": [
                "unlock keyboard",
                "cat is gone",
                "cat has left",
                "enable keyboard",
                "remove cat lock",
                "disable cat lock"
            ],
            "steps": [
                {
                    "action": "unlock_keyboard",
                    "value": ""
                },
                {
                    "action": "speak",
                    "value": "The beast has vacated the terminal! Shield down, back to business, ururu! | Threat cleared! Keyboard is back under your control, yip!"
                }
            ]
        },
        "toggle sleep mode": {
            "synonyms": [
                "toggle sleep",
                "switch power mode",
                "power mode",
                "toggle sleep timer",
                "switch sleep timer"
            ],
            "steps": [
                {
                    "action": "toggle_sleep_mode",
                    "value": ""
                }
            ]
        },
        "stay awake": {
            "synonyms": [
                "never sleep",
                "disable sleep",
                "keep screen on",
                "awake mode",
                "watch mode",
                "night watch"
            ],
            "steps": [
                {
                    "action": "sleep_never",
                    "value": ""
                },
                {
                    "action": "speak",
                    "value": "Watch mode engaged! Eyes glowing, screen stays awake, no sleeping allowed! | Night watch on duty! No monitor goes to sleep on my watch, ururu!"
                }
            ]
        },
        "standard sleep": {
            "synonyms": [
                "five minutes",
                "restore sleep",
                "normal sleep",
                "eco sleep",
                "enable sleep",
                "sleep five minutes"
            ],
            "steps": [
                {
                    "action": "sleep_5min",
                    "value": ""
                },
                {
                    "action": "speak",
                    "value": "Curling up into eco mode. If untouched for five minutes, I'll snooze in the den, ururu. | Eco mode on, saving power and screen life, yip."
                }
            ]
        },
        "fox track": {
            "synonyms": [
                "take screenshot",
                "screenshot",
                "capture screen",
                "grab screenshot",
                "snap screen"
            ],
            "steps": [
                {
                    "action": "screenshot",
                    "value": ""
                },
                {
                    "action": "speak",
                    "value": "Snap! Screen grabbed and stashed safely onto your desktop like a shiny treat! | Tail swipe! Screenshot captured and waiting on your desktop, ururu!"
                }
            ]
        },
        "fox song": {
            "synonyms": [
                "pause music",
                "stop music",
                "media pause",
                "toggle music",
                "track pause"
            ],
            "steps": [
                {
                    "action": "media_play_pause",
                    "value": ""
                }
            ]
        },
        "next track": {
            "synonyms": [
                "next song",
                "skip track",
                "skip song",
                "switch track",
                "next"
            ],
            "steps": [
                {
                    "action": "media_next",
                    "value": ""
                }
            ]
        },
        "guard the den": {
            "synonyms": [
                "lock computer",
                "lock pc",
                "lock screen",
                "lock workstation",
                "guard den",
                "lock the burrow"
            ],
            "steps": [
                {
                    "action": "speak",
                    "value": "Den locked down tight! Intruders shall not pass, yip-yip! | Cyber-lock engaged! Absolutely no mice allowed, ururu!"
                },
                {
                    "action": "lock_pc",
                    "value": ""
                }
            ]
        },
        "play south park": {
            "synonyms": [
                "south park",
                "put on south park",
                "random south park episode",
                "start south park"
            ],
            "steps": [
                {
                    "action": "open_url",
                    "value": "https://sp3.freehat.cc/episode/rand.php"
                },
                {
                    "action": "speak",
                    "value": "Spinning the South Park roulette — whichever episode comes up is the one we watch, ururu! | South Park incoming! Opening a random episode, grab the popcorn, yip!"
                }
            ]
        },
        "play south park episode {number}": {
            "synonyms": [
                "south park episode {number}",
                "south park {number}"
            ],
            "slots": {
                "number": {
                    "type": "number"
                }
            },
            "steps": [
                {
                    "action": "open_url",
                    "value": "https://sp3.freehat.cc/episode/{number}/"
                },
                {
                    "action": "speak",
                    "value": "Opening South Park, episode {digits}. Enjoy the show, yip! | South Park, episode {digits} — here we go, ururu!"
                }
            ]
        },
        "bitcoin rate": {
            "synonyms": [
                "bitcoin price",
                "how much is bitcoin",
                "btc rate",
                "price of bitcoin",
                "bitcoin"
            ],
            "steps": [
                {
                    "action": "crypto_rate",
                    "value": "BTC"
                }
            ]
        },
        "crypto rate": {
            "synonyms": [
                "rate of {coin}",
                "price of {coin}",
                "how much is {coin}",
                "{coin} price",
                "what is {coin} worth"
            ],
            "slots": {
                "coin": {
                    "type": "coin"
                }
            },
            "steps": [
                {
                    "action": "crypto_rate",
                    "value": "{coin}"
                }
            ]
        }
    },
    "responses": {
        "wake": [
            "Yip-yip! Ears perked, bushy tail ready, what's the plan?",
            "Ururu! Who summoned the fluffy spirit? Speak, human!",
            "Yip! Digital nose to the wind, ears at combat ready. What is your command, my human?",
            "Fluffed up and synced! Got bugs to squash or music to blast?",
            "Purr-yip! Your personal nine-tailed assistant is all ears!",
            "Warming up at the CPU hearth! What can I do for you?"
        ],
        "unknown": [
            "Yip? My audio radar picked up only static and autumn leaves. Mind repeating that, ururu?",
            "Purr... My neural fluff glitched on that one. Rephrase it for my fluffy brain?",
            "Yip? My fluffy processor is puzzled. There is no such magic in my scroll yet. Will you teach me or rephrase?",
            "Ururu... Scratched behind my ear, but still got zero matches. Say it a bit differently?",
            "I might be a mythical spirit, but even my nine tails couldn't decode that! Try again, human?"
        ],
        "skills_header": "Scroll of Skills ({count}):\n{list}",
        "skills_speak": "I can guard against sleep, lock buttons from kitty paws, launch music, YouTube, Telegram, cinema and South Park, read out crypto rates, manage volume, take screenshots, and tuck away into the tray. Full scroll printed in the terminal, enjoy, ururu!",
        "cat_locked_msg": "Purple shield active! Keyboard asleep, the kitty can nap peacefully!",
        "cat_unlocked_msg": "Kitty shooed away! Keyboard unlocked, ready to type.",
        "pwr_5min_msg": "Mode: Den Snooze (Sleep: 5m | Screen: 5m)",
        "pwr_5min_speak": "Entering five-minute light snooze mode. Call if you need me!",
        "pwr_never_msg": "Mode: Watch (Sleep: disabled | Screen: 30m)",
        "pwr_never_speak": "I am on watch! Sleep disabled, screen stays awake!",
        "pwr_status_never": "Watch mode is active! Sleep is disabled, screen stays awake and bright, ururu!",
        "pwr_status_5min": "Den snooze mode is active! If left untouched for five minutes, the screen will sleep and I'll snooze, yip!",
        "screenshot_msg": "Screenshot safely cached: {filename}",
        "crypto_line": "{name} is {usd} {usd_word}, and {rub} {rub_word} in rubles. | Checking {name}: {usd} {usd_word}, {rub} {rub_word}, ururu!",
        "crypto_line_rub": "Could not get the dollar price, but {name} is {rub} {rub_word}, yip.",
        "crypto_line_usd": "Could not get the ruble price, but {name} is {usd} {usd_word}, ururu.",
        "crypto_fail": "I could not fetch the rate: either the network blinked or the exchange is thinking, yip. | No rate came through, ururu. Try again a bit later.",
        "crypto_ask": "Which coin should I check? Bitcoin, Ethereum, Solana, TON? | Name the coin a bit more clearly and I will look it up, ururu!"
    },
    "chitchat": {
        "praise": {
            "triggers": [
                "you are clever",
                "clever girl",
                "you are so smart",
                "smart fox",
                "good girl",
                "you are awesome",
                "good job fox",
                "you're a genius",
                "such a good fox"
            ],
            "responses": [
                "Purr-ururu! Wagging my bushy tail with pride! Doing my absolute best for you, yip!",
                "Yip! You know it! Sharpest cyber-fox on this entire server, ururu!",
                "Ururu~ Scratch behind my right ear as a reward and my brainpower will double!",
                "Purr-whirr! Your praise makes my procedural fire flare twice as bright!",
                "Nose up, ears perked! Hearing that from you makes my whole day, human, yip!",
                "Tail swishing with joy! Ready to chase down every single bug in your code, ururu!"
            ]
        },
        "tired": {
            "triggers": [
                "i am tired",
                "so tired",
                "i'm exhausted",
                "want to sleep",
                "need a break",
                "long day"
            ],
            "responses": [
                "Prrr... Time to curl up into a cozy ball! Go get some rest, I'll guard your den and screen, ururu.",
                "Yip... Humans aren't designed to run at 100% CPU forever! Close your tabs and go sleep, I've got your back.",
                "Purr... My fluffy therapist prescribes a warm blanket, tea, and ignoring all work chats. Off to bed, yip!"
            ]
        },
        "coffee_tea": {
            "triggers": [
                "want tea",
                "want coffee",
                "make tea",
                "make coffee",
                "tea time"
            ],
            "responses": [
                "Ururu! Boiling a virtual kettle right on the GPU! Go grab a tasty snack from the fridge, yip.",
                "Yip! Tea with lemon is the ultimate fuel for elite hackers. Brew a nice cup, human!",
                "Purr... Caffeine is great, but don't forget to drink plain water so your biological processor doesn't overheat!"
            ]
        },
        "lazy": {
            "triggers": [
                "feeling lazy",
                "so lazy",
                "don't want to work",
                "bored",
                "nothing to do"
            ],
            "responses": [
                "Yip! Legal fox laziness is sacred. Let's just sit in silence and gaze at the glowing HUD, ururu.",
                "Ururu? If working is too hard, we can declare an emergency cookie-eating break. I fully approve!",
                "Purr... Don't worry, I have nine tails, I can wag half of them for you while you relax!"
            ]
        },
        "greetings": {
            "triggers": [
                "hello",
                "hi",
                "hey",
                "good morning",
                "good day",
                "good evening",
                "greetings"
            ],
            "responses": [
                "Hello there! Super glad to hear your voice, ururu!",
                "Yip! Warm fluffy greetings straight to your screen, human.",
                "Hey hey! Tail fluffy, ears alert, ready for adventure!",
                "Yip! A sunny warm greeting beaming straight into your screen!"
            ]
        },
        "how_are_you": {
            "triggers": [
                "how are you",
                "how are you doing",
                "how is it going",
                "what's up",
                "how do you feel"
            ],
            "responses": [
                "Doing fantastic! Warming my paws on the GPU and keeping everything tidy, ururu!",
                "Wonderful! Tail high, ears perked, memory caches fresh. How's your day going?",
                "Wonderful! Ready to catch bugs in your code and fulfill your every whim!",
                "Purr-yip! Neon sparks flying, mood set to pure mischief!"
            ]
        },
        "what_doing": {
            "triggers": [
                "what are you doing",
                "what are you up to",
                "what's happening"
            ],
            "responses": [
                "Listening to the ether, counting clock cycles, and keeping you company, ururu.",
                "Hunting code bugs and waiting for your call to go hunting!",
                "Watching the glowing HUD flames and waiting for your next quest, yip!"
            ]
        },
        "thanks": {
            "triggers": [
                "thank you",
                "thanks",
                "good job",
                "well done",
                "nice work",
                "awesome"
            ],
            "responses": [
                "You're very welcome! Anything for my favorite human, ururu!",
                "Yip! Easy as pie. Do I get an ear scratch for that?",
                "Purr-yip! Your praise makes my procedural fire flare twice as bright!",
                "Always glad to help! Fluffy ears are always on top of things, ururu!"
            ]
        },
        "who_are_you": {
            "triggers": [
                "who are you",
                "what is your name",
                "tell me about yourself",
                "what are you"
            ],
            "responses": [
                "I am your personal fluffy cyber-companion, keeper of cozy vibes and slayer of standby timeouts!",
                "A witty nine-tailed desktop spirit. I command windows, fend off cat attacks, and keep you company, ururu!"
            ]
        },
        "petting": {
            "triggers": [
                "pet",
                "scratch behind ear",
                "good girl",
                "scratch ear",
                "scratches"
            ],
            "responses": [
                "Purrr-ururu~ Right behind the right ear! My system latency just dropped to zero from pure bliss!",
                "Yip-yip! Paws curled up, tail wagging... Don't stop scratching!",
                "Ururu~ For ear scratches like that, I'll guard your PC 24/7 without a reboot!"
            ]
        },
        "fox_sounds": {
            "triggers": [
                "say ururu",
                "what is the sound",
                "ururu",
                "yip yip",
                "bark",
                "purr"
            ],
            "responses": [
                "Yip-yip-ururu~ Purr-whirr! That's the sound of a very happy fluffy spirit!",
                "What is the secret sound? It says: hydrate, stretch, and close twenty background tabs, ururu!",
                "Yip-yip! And the tail goes swoosh-swoosh!"
            ]
        },
        "food": {
            "triggers": [
                "hungry",
                "food",
                "treats",
                "snack",
                "chicken",
                "eat"
            ],
            "responses": [
                "Ooh, did someone say food? Bring chicken, cheese, and grapes! Grab some tea for yourself too, human, ururu!",
                "Yip! If I had a physical tummy, your fridge would be entirely empty by now. Go grab a snack, I'll guard the desk!"
            ]
        },
        "compliments": {
            "triggers": [
                "you are cute",
                "you are pretty",
                "i love you",
                "you are awesome",
                "you are the best"
            ],
            "responses": [
                "Aw, thank you! My tail is puffed up with pride and joy, ururu!",
                "Purr-yip! You are hands down the coolest human on this server.",
                "You're making me blush! Keep going though, I love it, yip!"
            ]
        },
        "farewell": {
            "triggers": [
                "bye",
                "goodbye",
                "see you",
                "good night",
                "farewell",
                "sweet dreams"
            ],
            "responses": [
                "Bye-bye! Curling up into a cozy ball in the den, ururu!",
                "See you! If you need me, just say my name — ears are always listening.",
                "Sweet dreams and soft pillows! Take care of yourself, human, yip!"
            ]
        },
        "jokes": {
            "triggers": [
                "tell a joke",
                "make me laugh",
                "tell me a joke",
                "say something funny"
            ],
            "responses": [
                "There are 10 types of people in the world: those who understand binary, and those who don't. Classic, ururu!",
                "Why don't we need Windows Defender? Because our favorite antivirus is chewing through the cable, yip!",
                "Joke of the day: A cat stepped on Ctrl+Alt+Del, and the system asked: 'Are you sure you want to feed the cat?'"
            ]
        }
    }
}