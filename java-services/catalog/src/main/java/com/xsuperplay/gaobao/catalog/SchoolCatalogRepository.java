package com.xsuperplay.gaobao.catalog;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.regex.Pattern;

import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Repository;
import org.springframework.web.server.ResponseStatusException;

/**
 * Read-only, parameterized MySQL projection over existing SQLAlchemy tables.
 * Do not return private conversations or use synthetic records as real advice.
 */
@Repository
public class SchoolCatalogRepository {
    private static final Pattern CURSOR = Pattern.compile("[0-9]{1,19}");

    private static final String FIELDS = """
            SELECT s.id, s.name, s.province, s.city, s.level, s.school_type,
                   s.ranking, s.is_985, s.is_211, s.is_double_first_class,
                   s.special_type, s.data_source_note,
                   (SELECT COUNT(*) FROM admission_scores a WHERE a.school_id = s.id) AS score_count,
                   (SELECT COUNT(*) FROM enrollment_plans p WHERE p.school_id = s.id) AS plan_count,
                   (SELECT MAX(a.year) FROM admission_scores a WHERE a.school_id = s.id) AS latest_year,
                   (SELECT MIN(a.min_score) FROM admission_scores a
                      WHERE a.school_id = s.id AND a.year = (
                        SELECT MAX(a2.year) FROM admission_scores a2 WHERE a2.school_id = s.id
                      )) AS latest_min_score
              FROM schools s
            """;
    private final NamedParameterJdbcTemplate jdbc;

    public SchoolCatalogRepository(NamedParameterJdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public Map<String, Object> list(String q, String province, String level,
                                    String schoolType, int limit, String cursor) {
        if (limit < 1 || limit > 200) {
            throw badRequest("limit must be between 1 and 200");
        }
        validateLength(q, 64, "q");
        validateLength(province, 20, "province");
        validateLength(level, 20, "level");
        validateLength(schoolType, 20, "school_type");

        var args = new MapSqlParameterSource();
        var where = new StringBuilder(" WHERE 1=1");
        if (q != null && !q.isBlank()) {
            where.append(" AND (LOCATE(:q, s.name) > 0 OR LOCATE(:q, s.city) > 0)");
            args.addValue("q", q.trim());
        }
        if (province != null && !province.isBlank()) {
            where.append(" AND s.province = :province");
            args.addValue("province", province);
        }
        if (level != null && !level.isBlank()) {
            where.append(" AND s.level = :level");
            args.addValue("level", level);
        }
        if (schoolType != null && !schoolType.isBlank()) {
            where.append(" AND s.school_type = :schoolType");
            args.addValue("schoolType", schoolType);
        }

        long total = jdbc.queryForObject("SELECT COUNT(*) FROM schools s" + where, args, Long.class);
        var pagedWhere = new StringBuilder(where);
        if (cursor != null && !cursor.isEmpty()) {
            if (!CURSOR.matcher(cursor).matches()) {
                throw badRequest("Invalid cursor");
            }
            try {
                args.addValue("cursor", Long.parseLong(cursor));
            } catch (NumberFormatException e) {
                throw badRequest("Cursor out of range");
            }
            pagedWhere.append(" AND s.id > :cursor");
        }
        args.addValue("pageSize", limit + 1);
        List<Map<String, Object>> rows = jdbc.queryForList(
                FIELDS + pagedWhere + " ORDER BY s.id ASC LIMIT :pageSize", args);

        boolean hasMore = rows.size() > limit;
        var items = new ArrayList<Map<String, Object>>(Math.min(limit, rows.size()));
        for (int i = 0; i < Math.min(limit, rows.size()); i++) {
            items.add(normalize(rows.get(i)));
        }
        String nextCursor = hasMore ? String.valueOf(items.get(items.size() - 1).get("id")) : null;

        var result = new LinkedHashMap<String, Object>();
        result.put("items", items);
        result.put("total", total);
        result.put("count", items.size());
        result.put("next_cursor", nextCursor);
        result.put("has_more", hasMore);
        return result;
    }

    public Optional<Map<String, Object>> get(long schoolId) {
        var args = new MapSqlParameterSource().addValue("id", schoolId);
        var rows = jdbc.queryForList(FIELDS + " WHERE s.id = :id", args);
        return rows.isEmpty() ? Optional.empty() : Optional.of(normalize(rows.get(0)));
    }

    private static void validateLength(String value, int max, String field) {
        if (value != null && value.length() > max) {
            throw badRequest(field + " exceeds " + max + " characters");
        }
    }

    private static ResponseStatusException badRequest(String message) {
        return new ResponseStatusException(HttpStatus.BAD_REQUEST, message);
    }

    private static Map<String, Object> normalize(Map<String, Object> raw) {
        var row = new LinkedHashMap<String, Object>();
        raw.forEach((key, value) -> row.put(key.toLowerCase(Locale.ROOT), value));
        for (String name : List.of("is_985", "is_211", "is_double_first_class")) {
            Object value = row.get(name);
            row.put(name, value instanceof Number number && number.intValue() != 0);
        }
        row.put("is_synthetic", "SYNTHETIC_DEMO_DATA".equals(row.get("special_type")));
        return row;
    }
}
