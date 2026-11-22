package com.projectv.dream;

import java.util.List;
import java.util.Map;

/**
 * The Dream Short looks (split out of DreamService, 22 Nov 2026): what the writer aims for, the style words every picture
 * and motion prompt ends with, the writer's structure templates, and the frame maths of Wan clips. Pure data, no I/O.
 */
public final class DreamLooks {

    private DreamLooks() {
    }

    static final int FPS = 16;          // Wan's own frame rate; the finish makes it 24
    static final int MIN_FRAMES = 33;
    static final int MAX_FRAMES = 81;

    /**
     * A look: what the writer aims for, how the shots relate ({@code structure}), what every picture and clip prompt
     * ends with, how long a shot is (0 = the configured default) and how shots join (0 = hard cut).
     */
    record Look(String brief, String pictureStyle, String motionStyle, String structure, double shotSeconds, double dissolveSec,
                double speed, String handheld) {
        Look(String brief, String pictureStyle, String motionStyle) {
            this(brief, pictureStyle, motionStyle, ONE_WORLD, 0, 0.5, 1.0, "none");
        }
    }

    /**
     * How a person films (owner, 19 Nov 2026: "not like a human holding a camera ... more like an AI video"). Wan
     * glides like a drone or dolly; these words pull it towards a phone in someone's hand. Every realistic look's
     * camera prompt ends with it.
     */
    static final String HUMAN_CAMERA = "handheld first-person view at eye level, empty scene, nobody is ever visible (19 Nov: 'a person' and 'holding "
            + "a phone' in the prompt made Wan add a walking woman, then a man filming): small natural sway, nothing gliding; "
            + "no drone, no dolly, no crane, no zoom, no flying camera";

    /** Small text comes out garbled; signs stay big and face the camera. */
    static final String SIGNS = "at most one large sign facing the camera, no small labels, no small text";

    static final String ONE_WORLD = "Invent ONE coherent world and %d shots that move through it like a dream, each a single "
            + "continuous camera move.";

    /** Order shown on the new-Short form; the first is the default. */
    public static final List<String> LOOK_ORDER = List.of("lonely", "drive", "walkthrough", "dreamcore", "impossible");

    /** What the new-Short form offers: the order above (the Autopilot rotates only through that) plus looks picked by hand. */
    public static final List<String> SELECTABLE_LOOKS = List.of("lonely", "drive", "walkthrough", "dreamcore", "impossible",
            "architecture", "unfiltered");

    /**
     * Owner, 20 Nov 2026: a general generator that makes exactly what he wrote. No writer, no look style or camera rules, no
     * "no people", no lessons, no Scout, no quality judging, no grading or haze at the finish. One line of the prompt = one shot,
     * used word for word as both the picture prompt and the motion prompt.
     */
    public static final String UNFILTERED = "unfiltered";

    static boolean unfiltered(Map<String, Object> d) {
        return UNFILTERED.equals(d.get("look"));
    }

    /**
     * The owner's rule for every Dream Short (19 Nov 2026): it may look different each time, but it must feel good or
     * nostalgic, have the best possible picture, and look real, never obviously AI.
     */
    static final String FEEL = "Feeling: warm, nostalgic, feel-good, calm wonder; beautiful and a little strange, never "
            + "scary, sad or gloomy. It must look REAL, like footage a person actually filmed: believable places, true-to-life "
            + "scale and light, small real-world imperfections; never like a 3D render, a game or an obvious AI image. "
            // 19 Nov test: the writer asked for drone, dolly and orbit moves and a Coca-Cola logo.
            + "Every camera move is something a person holding a phone can do: stand still, take a step, walk slowly, "
            + "turn their head, crouch; never a drone, dolly, crane, orbit, time-lapse or zoom. The person filming is never seen and "
            + "no people appear in any shot. No brand names or logos. In each shot's motion prompt describe only how the view "
            + "moves (e.g. 'handheld, slow step forward, slight sway'); never write the words phone, person, hand, someone or "
            + "holding (19 Nov: 'phone held at chest height' made Wan add a man filming).";

    /**
     * Feeling and camera rule of the impossible-architecture look (idea pack): the same calm, real-looking wonder, but the
     * camera glides continuously through the scene (a slow drift, tilt or turn), as in the studio's reference Shorts.
     */
    static final String FEEL_ARCHITECTURE = "Feeling: calm wonder, beautiful and impossible, never scary or gloomy. Every "
            + "shot is one believable, photo-real piece of city (real materials, real light, true-to-life scale) whose geometry "
            + "breaks the rules in ONE clear way. The camera glides slowly and continuously through the scene: a steady drift "
            + "forward, a slow tilt or a gentle turn; no cuts, no zoom, no shaking. No people, no brand names or logos, no "
            + "readable text. In each shot's motion prompt describe only how the view moves and how the impossible geometry slowly "
            + "shifts (never the words phone, person, hand, someone or holding).";

    /** Writer structure when the owner chains the shots: one unbroken take, each shot starts where the last ended. */
    static final String CHAIN_STRUCTURE = "Invent %d shots that are consecutive moments of ONE continuous camera move through ONE "
            + "world: each shot starts exactly where the previous shot ended (its first frame IS the previous shot's last "
            + "frame) and the camera keeps travelling the same way, so the Short plays as a single unbroken take. Never cut "
            + "to another place or time of day. In each shot's scene describe what the camera sees at the END of that "
            + "shot; in its motion describe the same continuous movement (direction and speed) carrying on.";

    /** Writer structure for first-last frame chaining: each picture is both the start of its shot and the end of the one before. */
    static final String BRIDGE_STRUCTURE = "Invent %d shots that are consecutive moments of ONE continuous camera move through ONE "
            + "world. Each shot's picture is the view at the START of that shot and, at the same time, the exact view at the "
            + "END of the shot before it (the video travels from one picture to the next), so neighbouring pictures must be "
            + "close: the same place seen a little further along the same path, with the same light and time of day. Never "
            + "cut to another place. In each shot's scene describe what the camera sees at the START of that shot; in its "
            + "motion describe the movement (direction and speed) that carries the camera on to the next shot's view.";

    /** Writer structure for chains of N shots: inside a group one unbroken take, between groups a new view of the same world. */
    static final String CHAIN_GROUP_STRUCTURE = "Invent %d shots in the same world, in scenes of %d shots each. The %d shots of a scene "
            + "are ONE continuous camera move: each starts exactly where the previous one ended (its first frame IS the previous "
            + "shot's last frame) and the camera keeps travelling the same way. A new scene (the next group) begins as a fresh view "
            + "of the same world: another angle, place or moment, with a cut from the scene before. In each shot's scene "
            + "describe what the camera sees at the END of that shot; in its motion the same continuous movement carrying on.";

    /** Locked scene: one camera path for the whole Short, planned first, and the shots are consecutive segments of it. */
    static final String LOCKED_STRUCTURE = "Invent ONE locked scene, like a single camera move through one 3D set: first plan ONE "
            + "camera path for the whole Short (a single flowing route, e.g. forward down the avenue, rising, then turning left), then "
            + "cut it into %d consecutive segments of equal length. Every shot is the next segment of that same path in the SAME "
            + "world: it starts exactly where the previous shot ended (its first frame IS the previous shot's last frame) and the "
            + "camera keeps the same direction and speed. Never cut to another place, time of day or style. In each shot's scene "
            + "describe what the camera sees at the END of that segment; in its motion only that segment of the one path.";

    static final String CAMERA_PATH_FIELD = ", \"cameraPath\": the one camera path of the whole Short in a single sentence";

    /** Asked of the writer only when the Short has a hook title (setting off = the prompt is unchanged). */
    static final String HOOK_FIELD = ", \"hook\": a 3 to 6 word hook title for the first two seconds, upper case, written to make "
            + "a viewer curious about THIS Short (a surprising claim about the place, not a description), with exactly ONE key "
            + "word between asterisks, e.g. THE STREET *FOLDED* LIKE PAPER; no brand names";

    /** Ends every picture prompt of the realistic looks: what makes a still read as a real photo, not an AI image. */
    static final String REAL_PHOTO = "real photograph, natural light falloff, real-world wear and small imperfections "
            + "(dust, scuffs, uneven grass, puddles), true-to-life scale, natural colours that are vivid but not over-processed HDR, "
            + "not a 3D render, not plastic, not airbrushed, not perfectly symmetrical";

    static final Map<String, Look> LOOKS = Map.of(
            // Owner's examples (18 Nov 2026): one ordinary object alone and glowing in a vast empty place, a different
            // place every shot, one feeling; sharp photo-real, vivid golden/blue-hour and neon light; slow push-in; hard cuts.
            "lonely", new Look(
                    "Lonely object (parallel-world core): each shot is ONE ordinary man-made object or small building "
                            + "(vending machine, phone booth, neon kiosk, gas station, traffic light, bus stop, arcade cabinet, "
                            + "empty classroom, pool hallway) standing alone in a vast empty real place (flower field, cornfield, "
                            + "open road, prairie, beach, a sea of clouds), at golden hour, sunset, blue hour or night, glowing "
                            + "(neon, warm windows, a street lamp, fluorescent light) with wet shiny reflections. Nostalgic, "
                            + "calm, lonely, beautiful. "
                            + "No people, no animals, no readable words except at most one short sign.",
                    "photograph, shot on a full-frame camera with a wide lens, photorealistic, tack sharp, vivid saturated "
                            + "colours, golden hour, blue hour or night sky with glowing clouds, neon or warm lamp glow, wet "
                            + "reflections, high detail, cinematic, vertical composition with the object in the lower middle, "
                            + "no people, " + SIGNS + ", " + REAL_PHOTO,
                    "first-person view: we stand still looking at the object, maybe one slow step closer; the camera barely moves; the "
                            + "scene itself is alive: clouds drift across the sky, neon and lamps flicker and pulse softly, grass "
                            + "and flowers sway in the breeze, water ripples; the object stays still; no cuts, no people; "
                            + HUMAN_CAMERA,
                    "Invent %d shots, each a DIFFERENT place and a DIFFERENT object, joined by one feeling (not one connected "
                            + "world). Vary the object, the landscape and the time of light from shot to shot.",
                    3.5, 0, 0.7, "stand"),
            // Owner's example (18 Nov 2026): first-person walk through ordinary interiors where one impossible thing
            // happens (kitchen opening onto grass, hallway flooding into a sea, stairs melting into rainbow paint).
            "walkthrough", new Look(
                    "Walk-through (liminal fever dream): first-person walk through ordinary empty interiors (kitchen, "
                            + "hallway, stairwell, pool room, school corridor, hotel lobby) in pastel pink, mint and sky blue, "
                            + "where ONE impossible thing happens in each (a doorway opening onto a meadow, the floor flooding "
                            + "into a sea, stairs melting into rainbow paint, a night sky behind an exit door). Bright, glossy, "
                            + "strange but calm. No people, no text.",
                    "phone camera photo, first-person point of view at eye level, wide lens, photorealistic, sharp, bright "
                            + "fluorescent and daylight, pastel pink mint and sky-blue colours, glossy wet tiled floors with "
                            + "reflections, liminal space, no people, " + SIGNS + ", " + REAL_PHOTO,
                    "first-person view: we walk slowly forward at a relaxed pace, gentle up-and-down step bob, glancing a little left "
                            + "and right; water ripples and lights shimmer; continuous, no cuts, no people in view; " + HUMAN_CAMERA,
                    "Invent %d rooms or corridors walked through one after another, each a different ordinary place with "
                            + "its own impossible thing; the walk keeps going forward from shot to shot.",
                    5.0, 0.35, 0.75, "walk"),
            // Owner's examples (19 Nov 2026): dashcam on a highway that curves up into space; side window passing a
            // lonely house on a snowy field at blue hour. Real road details make the impossible sight believable.
            "drive", new Look(
                    "Drive-by: filmed from inside a car, either through the windscreen (dashcam) or out of the side window "
                            + "(the side mirror or window frame in view), on an ordinary road where ONE impossible, beautiful "
                            + "sight goes by (the highway curving up off the Earth into space, a lonely lit house on a frozen sea "
                            + "at sunset, a lighthouse standing on a cloud). Real road details (lane markings, guard rails, "
                            + "roadside poles, a few ordinary cars) make it believable. No readable words.",
                    "photo taken from inside a car, dashcam view through the windscreen or side-window view with the side "
                            + "mirror and window frame in frame, phone or dashcam lens, photorealistic, sharp, natural daylight "
                            + "or golden or blue hour, vivid sky, no people visible, " + SIGNS + ", " + REAL_PHOTO,
                    "filmed on a phone held by a passenger in a car driving at normal speed: light road vibration and small "
                            + "bumps; the landscape passes with natural parallax, lane markings and roadside poles move by, other "
                            + "cars keep driving; continuous, no cuts; no drone, no flying camera",
                    "Invent %d shots, each a different drive with its own impossible sight; the camera is inside a car in "
                            + "every shot (mix windscreen and side-window views).",
                    5.0, 0, 0.85, "car"),
            "dreamcore", new Look(
                    "Dreamcore / liminal fever dream: one soft pastel world (pink, mint, cream, sky blue), surreal oversized "
                            + "or out-of-place objects, water, tiles, clouds, empty calm places, nostalgic and slightly eerie. "
                            + "No people, no text, no brands.",
                    "dreamcore, liminal space, pastel pink and mint palette, soft hazy daylight, 35mm film photo, slight "
                            + "lens blur, nostalgic, surreal, calm, empty, no people, no text",
                    "slow continuous camera move, like walking forward in a dream; gentle, smooth, no cuts, no people"),
            // Idea pack "Impossible architecture": photo-real cities that break the rules, one continuous drifting camera.
            "architecture", new Look(
                    "Impossible architecture (dreamcore city): one photo-real city scene (towers, bridges, streets, stairs) whose "
                            + "geometry breaks the rules in one clear way (twisting towers, a looping bridge, a folding street, "
                            + "stairs into the sky). Vertical wide compositions, dusk or golden light, believable materials. "
                            + "No people, no text, no brands.",
                    "photograph, photorealistic, cinematic vertical wide shot of impossible architecture, dramatic dusk or golden "
                            + "light, rich architectural detail, believable materials and scale, soft atmospheric haze, no people, "
                            + "no text, no logos, " + REAL_PHOTO,
                    "slow continuous camera glide through the scene (steady drift forward, slow tilt or gentle turn), the impossible "
                            + "geometry slowly shifts and warps, lights flicker softly, no cuts, no zoom, no people",
                    "Invent %d shots that move through ONE impossible city world, each a single continuous camera move, "
                            + "every shot showing a new angle of the same warping place.",
                    2.5, 0, 1.0, "none"),
            "impossible", new Look(
                    "Impossible place: one photo-real scene where two worlds that never meet are joined (e.g. a roadside "
                            + "diner on the Moon), believable light and scale, wide views, awe. No people close up, no text, no brands.",
                    "photorealistic, cinematic wide shot, believable lighting and scale, rich detail, no text",
                    "slow cinematic camera move (push-in, drift or crane), subtle life in the scene, no cuts"),
            // Nothing is added to the owner's words (see UNFILTERED): empty style suffixes, hard cuts, normal speed.
            // 5 s = Wan's longest clip (81 frames at 16 fps): each line of the owner's script gets the most screen time it can.
            UNFILTERED, new Look("Unfiltered: the owner's own prompt, exactly as written.", "", "", ONE_WORLD, 5.0, 0, 1.0, "none"));

    static Look look(String id) {
        return LOOKS.getOrDefault(id == null ? LOOK_ORDER.getFirst() : id, LOOKS.get(LOOK_ORDER.getFirst()));
    }

    /** 4n+1 frames covering the shot at 16 fps, within Wan's ~2-5 s range. */
    static int frames(double seconds) {
        int n = (int) Math.ceil(seconds * FPS / 4.0) * 4 + 1;
        return Math.max(MIN_FRAMES, Math.min(MAX_FRAMES, n));
    }

    /** Vertical clip size for a quality setting: 480p = 480x832 (Wan fast's native), 720p = 720x1280. */
    static int[] clipSize(String quality) {
        return "720p".equals(quality) ? new int[]{720, 1280} : new int[]{480, 832};
    }

}
