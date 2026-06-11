-- Mart: underserved counties — those below the coverage or density threshold.
-- Used for Page 3 of the dashboard.

with index_data as (
    select
        county_name,
        health_access_index,
        access_tier,
        coverage_ratio_10km,
        coverage_pct,
        facilities_per_100k,
        nearest_hospital_km,
        total_facilities,
        hospital_count,
        population_2023,
        area_km2,
        index_rank
    from {{ ref('county_health_index') }}
),

spatial as (
    select
        county_name,
        is_underserved
    from raw_spatial_metrics
),

gaps as (
    select
        i.county_name,
        i.health_access_index,
        i.access_tier,
        i.coverage_pct,
        i.facilities_per_100k,
        i.nearest_hospital_km,
        i.total_facilities,
        i.hospital_count,
        i.population_2023,
        i.area_km2,
        i.index_rank,
        coalesce(s.is_underserved, true)              as is_underserved,
        -- Recommended additional facilities to reach 2/100k threshold
        greatest(
            0,
            ceiling(i.population_2023 * 2.0 / 100000.0 - i.total_facilities)
        )::integer                                    as facilities_needed,
        -- Gap severity score (0=fine, higher=worse)
        round(
            (1.0 - i.coverage_ratio_10km) * 50
            + greatest(0.0, 2.0 - i.facilities_per_100k) * 25
            + least(i.nearest_hospital_km / 100.0, 1.0) * 25
        , 1)                                          as gap_severity_score
    from index_data i
    left join spatial s on s.county_name = i.county_name
    where coalesce(s.is_underserved, true) = true
       or i.access_tier in ('Low', 'Critical')
)

select * from gaps
order by gap_severity_score desc
