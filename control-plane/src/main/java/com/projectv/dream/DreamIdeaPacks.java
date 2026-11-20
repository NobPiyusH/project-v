package com.projectv.dream;

import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.json.JsonMapper;

import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.util.List;
import java.util.Optional;

/**
 * Curated idea packs for Dream Shorts ({@code dream/idea-packs.json}): concept seeds with a hook title and a camera note
 * that the writer draws from instead of the Scout researching. Plain data, loaded once; every idea is original to the
 * studio (no creator's text is copied).
 */
public final class DreamIdeaPacks {

    /** One concept seed. {@code hook}: the title card text, the starred word is the big coloured one. */
    public record Idea(String id, String title, String concept, String hook, String camera, String palette) {
    }

    /** {@code look}: the Dream look this pack is made in (its picture style, camera rules and finish). */
    public record Pack(String id, String name, String look, String description, List<Idea> ideas) {
    }

    record Packs(List<Pack> packs) {
    }

    private static final String RESOURCE = "/dream/idea-packs.json";

    private static volatile List<Pack> loaded;

    private DreamIdeaPacks() {
    }

    /** All packs (loaded once from the classpath). */
    public static List<Pack> packs() {
        List<Pack> p = loaded;
        if (p == null) {
            try (InputStream in = DreamIdeaPacks.class.getResourceAsStream(RESOURCE)) {
                if (in == null) {
                    throw new IllegalStateException(RESOURCE + " is missing");
                }
                p = parse(JsonMapper.builder().build(), in);
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
            loaded = p;
        }
        return p;
    }

    static List<Pack> parse(ObjectMapper json, InputStream in) throws IOException {
        Packs file = json.readValue(in, Packs.class);
        return file.packs() == null ? List.of() : List.copyOf(file.packs());
    }

    public static Optional<Pack> pack(String packId) {
        return packs().stream().filter(p -> p.id().equals(packId)).findFirst();
    }

    public static Optional<Idea> idea(String packId, String ideaId) {
        return pack(packId).flatMap(p -> p.ideas().stream().filter(i -> i.id().equals(ideaId)).findFirst());
    }

    /** The reference stored on the Short: "pack/idea". */
    public static String ref(String packId, String ideaId) {
        return packId + "/" + ideaId;
    }

    /** The idea a stored reference points to; empty for null or an idea that no longer exists. */
    public static Optional<Idea> resolve(String ref) {
        if (ref == null || !ref.contains("/")) {
            return Optional.empty();
        }
        int slash = ref.indexOf('/');
        return idea(ref.substring(0, slash), ref.substring(slash + 1));
    }

    /** What the writer reads for a seeded Short: the concept, its camera note and palette. */
    public static String forWriter(Idea idea) {
        return "\n\nConcept seed from the studio's idea pack (build the Short on this instead of inventing a different idea): "
                + idea.title() + " - " + idea.concept() + " Camera: " + idea.camera() + " Palette: " + idea.palette() + ".";
    }
}
