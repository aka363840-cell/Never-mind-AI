"""Extra content banks (facts, riddles, horoscope, trivia, jokes, game flavour text).
Pure data -- no imports from the rest of the bot, so any module can use it safely."""
from .facts import FACTS_MORE
from .horoscope import (CAREER, COLORS_MORE, FORTUNES_MORE, LOVE_MORE, LUCKY_TIMES, MONEY_MORE, MOOD_MORE, SIGN_VIBE,
                        TIPS_MORE)
from .riddles import RIDDLES_MORE, WYR_MORE
from .rpg import (BOSS_MORE, CRIME_LOSE_MORE, CRIME_WIN_MORE, DATE_MORE, EXP_TALES_MORE, FISH_EXTRA, HEIST_TARGETS_MORE,
                  MINE_EXTRA, WORK_MORE)
from .social import (BALL_MORE, COMPLIMENTS_MORE, DARE_MORE, GOODBYE_MORE, JOKES_MORE, MOODS_MORE, ROASTS_MORE,
                     TRUTH_MORE, WELCOME_MORE)
from .trivia import QUESTIONS_MORE
