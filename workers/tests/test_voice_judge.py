"""The narration quality gate: real failures from the first 'Life of a star' run must fail, real good takes pass."""

from workers.activities.voice import judge

GOOD = [
    ("In zero point two five seconds, gravity crushes the core at twenty-three percent the speed of light.",
     "In 0.25 seconds, gravity crushes the core at 23% the speed of light."),
    ("An Earth-sized core of pure iron, holding one point four solar masses, stands defenseless.",
     "An Earth-sized core of pure iron holding 1.4 solar masses stands defenseless."),
    ("A supersonic shockwave blasts outward at thirty thousand kilometers per second, outshining one hundred billion stars.",
     "A supersonic shockwave blasts outward at 30,000 kilometers per second, outshining 100 billion stars."),
    ("For ten million years, it reigns in magnificent equilibrium.",
     "For ten million years, it reigned in magnificent equilibrium."),
    ("In the cold dark between stars, a cloud begins to fall.", "In the cold dark between stars a cloud begins to fall"),
]

BAD = [
    # instructions read aloud (owner's complaint)
    ("A supersonic shockwave blasts outward at thirty thousand kilometers per second.",
     "A supersonic shockwave blasts outward at 30,000 kilometers per second. Quiet awe. Say only this. A supersonic shockwave"),
    ("From this cosmic engine, the elements of life are born.",
     "Deep reverent, slow-patient narration, infused with cosmic gravity, poetic space, and quiet awe. From this cosmic engine"),
    # cut off mid-sentence
    ("When contraction heats the core to ten million Kelvin, hydrogen fuses into helium and the star ignites.",
     "When contraction heats the core to 10 million"),
]


def test_good_takes_pass():
    for expected, heard in GOOD:
        ok, why = judge(expected, heard)
        assert ok, (why, heard)


def test_bad_takes_fail():
    for expected, heard in BAD:
        ok, why = judge(expected, heard)
        assert not ok, (why, heard)
