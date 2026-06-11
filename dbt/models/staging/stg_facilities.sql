-- Staging: clean and standardise raw health facility data.
-- Source: raw_facilities table populated by src/ingest_facilities.py

with source as (
    select
        facility_id,
        facility_name,
        facility_type,
        facility_level,
        owner,
        county,
        sub_county,
        latitude,
        longitude
    from raw_facilities
),

cleaned as (
    select
        facility_id,
        -- Strip whitespace and title-case facility name
        trim(facility_name)                           as facility_name,
        -- Standardised type from ingest
        facility_type,
        -- Level 2-6
        facility_level,
        trim(coalesce(owner, 'Unknown'))              as owner,
        trim(county)                                  as county,
        trim(coalesce(sub_county, ''))                as sub_county,
        round(latitude::double, 6)                    as latitude,
        round(longitude::double, 6)                   as longitude,
        -- Derived: is this a major referral facility?
        case
            when facility_level >= 5 then true
            else false
        end                                           as is_referral_hospital,
        -- Derived: is this a primary health facility (dispensary / health centre)?
        case
            when facility_level <= 3 then true
            else false
        end                                           as is_primary_care
    from source
    where
        facility_name is not null
        and latitude  is not null
        and longitude is not null
        -- Kenya bounding box
        and latitude  between -5.0  and  5.0
        and longitude between 33.9  and  41.9
        -- Drop rows with clearly bad level
        and facility_level between 2 and 6
)

select * from cleaned
