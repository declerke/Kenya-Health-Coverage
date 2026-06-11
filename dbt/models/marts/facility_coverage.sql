-- Mart: facility counts and coverage metrics per county.
-- Joins staging facilities + counties + spatial metrics.

with facilities as (
    select
        county,
        facility_level,
        facility_type,
        is_referral_hospital,
        is_primary_care,
        facility_id
    from {{ ref('stg_facilities') }}
),

counties as (
    select
        county_name,
        area_km2,
        centroid_lat,
        centroid_lon,
        county_size_category
    from {{ ref('stg_counties') }}
),

population as (
    select
        county_name,
        population_2023
    from raw_county_population
),

spatial as (
    select
        county_name,
        coverage_ratio_10km,
        facilities_per_100k,
        nearest_hospital_km,
        health_access_index
    from raw_spatial_metrics
),

-- Aggregate facility counts per county
facility_counts as (
    select
        county,
        count(*)                                         as total_facilities,
        sum(case when facility_level >= 4 then 1 else 0 end) as hospital_count,
        sum(case when facility_level = 3  then 1 else 0 end) as health_centre_count,
        sum(case when facility_level <= 2 then 1 else 0 end) as dispensary_count,
        sum(case when is_referral_hospital then 1 else 0 end) as referral_hospital_count,
        sum(case when is_primary_care      then 1 else 0 end) as primary_care_count,
        round(avg(facility_level::double), 2)             as avg_facility_level
    from facilities
    group by county
),

combined as (
    select
        c.county_name,
        c.area_km2,
        c.centroid_lat,
        c.centroid_lon,
        c.county_size_category,
        coalesce(p.population_2023, 0)                   as population_2023,
        coalesce(fc.total_facilities, 0)                 as total_facilities,
        coalesce(fc.hospital_count, 0)                   as hospital_count,
        coalesce(fc.health_centre_count, 0)              as health_centre_count,
        coalesce(fc.dispensary_count, 0)                 as dispensary_count,
        coalesce(fc.referral_hospital_count, 0)          as referral_hospital_count,
        coalesce(fc.primary_care_count, 0)               as primary_care_count,
        coalesce(fc.avg_facility_level, 2.0)             as avg_facility_level,
        coalesce(sm.coverage_ratio_10km, 0.0)            as coverage_ratio_10km,
        coalesce(sm.facilities_per_100k, 0.0)            as facilities_per_100k,
        coalesce(sm.nearest_hospital_km, 999.0)          as nearest_hospital_km,
        coalesce(sm.health_access_index, 0.0)            as health_access_index,
        round(
            coalesce(fc.total_facilities, 0)::double
            / nullif(c.area_km2, 0) * 1000, 4
        )                                                as facilities_per_1000km2
    from counties c
    left join facility_counts fc on fc.county = c.county_name
    left join population       p  on p.county_name = c.county_name
    left join spatial          sm on sm.county_name = c.county_name
)

select * from combined
order by health_access_index desc
