-- Staging: clean county boundaries data.
-- Source: raw_counties table populated by src/ingest_boundaries.py

with source as (
    select
        county_name,
        area_km2,
        centroid_lat,
        centroid_lon,
        geometry_wkt
    from raw_counties
),

cleaned as (
    select
        trim(county_name)               as county_name,
        round(area_km2, 2)              as area_km2,
        round(centroid_lat, 6)          as centroid_lat,
        round(centroid_lon, 6)          as centroid_lon,
        geometry_wkt,
        -- Size category based on area
        case
            when area_km2 > 30000 then 'Large'
            when area_km2 > 5000  then 'Medium'
            else                       'Small'
        end                             as county_size_category
    from source
    where
        county_name is not null
        and area_km2 > 0
        and centroid_lat  between -5.0  and  5.0
        and centroid_lon  between 33.9  and  41.9
)

select * from cleaned
