package com.xsuperplay.gaobao.catalog;

import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.web.server.ResponseStatusException;
import static org.junit.jupiter.api.Assertions.*;

class SchoolCatalogRepositoryTest {
    private SchoolCatalogRepository repository;

    @BeforeEach
    void setUp() {
        var ds = new DriverManagerDataSource("jdbc:h2:mem:gaobao_catalog;MODE=MySQL;DB_CLOSE_DELAY=-1;NON_KEYWORDS=YEAR", "sa", "");
        var jdbc = new JdbcTemplate(ds);
        jdbc.execute("DROP TABLE IF EXISTS admission_scores");
        jdbc.execute("DROP TABLE IF EXISTS enrollment_plans");
        jdbc.execute("DROP TABLE IF EXISTS schools");
        jdbc.execute("""
                CREATE TABLE schools (
                    id BIGINT PRIMARY KEY, name VARCHAR(100), province VARCHAR(20), city VARCHAR(30),
                    level VARCHAR(20), school_type VARCHAR(20), ranking INT,
                    is_985 INT, is_211 INT, is_double_first_class INT,
                    special_type VARCHAR(50), data_source_note VARCHAR(200)
                )
                """);
        jdbc.execute("CREATE TABLE admission_scores (school_id BIGINT, year INT, min_score INT)");
        jdbc.execute("CREATE TABLE enrollment_plans (school_id BIGINT)");
        jdbc.update("""
                INSERT INTO schools VALUES
                (1,'星河大学（合成）','示例省','星城','普通','综合',NULL,0,0,0,'SYNTHETIC_DEMO_DATA','仅用于演示'),
                (2,'明川学院（合成）','示例省','明城','普通','理工',NULL,0,0,0,'SYNTHETIC_DEMO_DATA','仅用于演示'),
                (3,'自有数据大学','其他省','新城','双一流','综合',5,0,0,1,NULL,'须核验授权')
                """);
        jdbc.update("INSERT INTO admission_scores VALUES (1,2024,600),(1,2025,610),(1,2025,605)");
        jdbc.update("INSERT INTO enrollment_plans VALUES (1),(1)");
        repository = new SchoolCatalogRepository(new NamedParameterJdbcTemplate(ds));
    }

    @Test
    @SuppressWarnings("unchecked")
    void paginationPreservesTotalAndCursor() {
        var first = repository.list(null, null, null, null, 1, null);
        assertEquals(3L, first.get("total"));
        assertEquals(1, first.get("count"));
        assertEquals("1", first.get("next_cursor"));
        assertEquals(true, first.get("has_more"));
        var row = ((List<Map<String, Object>>) first.get("items")).get(0);
        assertEquals(3L, ((Number) row.get("score_count")).longValue());
        assertEquals(2L, ((Number) row.get("plan_count")).longValue());
        assertEquals(605, ((Number) row.get("latest_min_score")).intValue());
        assertEquals(true, row.get("is_synthetic"));
        assertEquals(false, row.get("is_985"));
        var next = repository.list(null, null, null, null, 2, "1");
        assertEquals(3L, next.get("total"));
        assertEquals(2, next.get("count"));
        assertEquals(false, next.get("has_more"));
        assertNull(next.get("next_cursor"));
    }

    @Test
    void parameterizedFiltersDoNotInterpretSql() {
        assertEquals(2, repository.list(null, "示例省", null, null, 20, null).get("count"));
        assertEquals(1, repository.list("星河", null, null, null, 20, null).get("count"));
        assertEquals(0, repository.list("' OR 1=1 --", null, null, null, 20, null).get("count"));
    }

    @Test
    void invalidCursorAndLimitsFailClosed() {
        assertThrows(ResponseStatusException.class,
                () -> repository.list(null, null, null, null, 20, "-1"));
        assertThrows(ResponseStatusException.class,
                () -> repository.list(null, null, null, null, 20, "9999999999999999999"));
        assertThrows(ResponseStatusException.class,
                () -> repository.list(null, null, null, null, 201, null));
        assertThrows(ResponseStatusException.class,
                () -> repository.list("x".repeat(65), null, null, null, 20, null));
    }

    @Test
    void detailsRetainProvenanceAndMissingRowsStayMissing() {
        var row = repository.get(3).orElseThrow();
        assertEquals(false, row.get("is_synthetic"));
        assertEquals("须核验授权", row.get("data_source_note"));
        assertTrue(repository.get(999).isEmpty());
    }
}
