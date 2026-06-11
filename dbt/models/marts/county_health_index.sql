-- Mart: composite health access index with WB indicators per county.
-- This is the primary table used for the choropleth map.

with coverage as (
    select
        county_name,
        total_facilities,
        hospital_count,
        coverage_ratio_10km,
        facilities_per_100k,
        nearest_hospital_km,
        health_access_index,
        population_2023,
        area_km2,
        centroid_lat,
        centroid_lon
    from {{ ref('facility_coverage') }}
),

wb as (
    select
        hospital_beds_per_1000,
        hospital_beds_year,
        physicians_per_1000,
        physicians_year,
        under5_mortality_rate,
        under5_mortality_year,
        maternal_mortality_ratio,
        maternal_mortality_year
    from {{ ref('stg_wb_health') }}
),

-- Rank counties by health access index
ranked as (
    select
        c.*,
        row_number() over (order by c.health_access_index desc) as index_rank,
        -- Add national WB indicators (same for all counties; county-level not available)
        wb.hospital_beds_per_1000,
        wb.hospital_beds_year,
        wb.physicians_per_1000,
        wb.physicians_year,
        wb.under5_mortality_rate,
        wb.under5_mortality_year,
        wb.maternal_mortality_ratio,
        wb.maternal_mortality_year,
        -- Health access tier
        case
            when c.health_access_index >= 60 then 'High'
            when c.health_access_index >= 40 then 'Medium'
            when c.health_access_index >= 20 then 'Low'
            else                                  'Critical'
        end                                               as access_tier,
        -- Coverage band
        round(c.coverage_ratio_10km * 100, 1)            as coverage_pct
    from coverage c
    cross join wb
)

select * from ranked
order by index_rank
