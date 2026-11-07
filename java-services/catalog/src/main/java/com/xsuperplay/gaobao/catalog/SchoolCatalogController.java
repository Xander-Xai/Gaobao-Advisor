package com.xsuperplay.gaobao.catalog;

import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/**
 * New, additive public read API. The existing Python /api/v1/data endpoints
 * remain authoritative, and AI recommendations continue to run in LangGraph.
 */
@RestController
@RequestMapping("/api/v1/catalog")
public class SchoolCatalogController {
    private final SchoolCatalogRepository schools;

    public SchoolCatalogController(SchoolCatalogRepository schools) {
        this.schools = schools;
    }

    @GetMapping("/schools")
    public Map<String, Object> listSchools(
            @RequestParam(required = false) String q,
            @RequestParam(required = false) String province,
            @RequestParam(required = false) String level,
            @RequestParam(name = "school_type", required = false) String schoolType,
            @RequestParam(defaultValue = "20") int limit,
            @RequestParam(required = false) String cursor) {
        return schools.list(q, province, level, schoolType, limit, cursor);
    }

    @GetMapping("/schools/{schoolId}")
    public Map<String, Object> getSchool(@PathVariable long schoolId) {
        if (schoolId < 1) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Invalid school id");
        }
        return schools.get(schoolId).orElseThrow(
                () -> new ResponseStatusException(HttpStatus.NOT_FOUND, "School not found"));
    }
}
