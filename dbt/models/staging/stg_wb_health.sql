-- Staging: pivot World Bank health indicator time-series from long to wide,
-- keeping only the most recent non-null value per indicator.
-- Source: raw_wb_indicators table populated by src/ingest_worldbank.py

with source as (
    select
        indicator_code,
        indicator_label,
        year,
        value
    from raw_wb_indicators
    where value is not null
),

-- Most-recent value per indicator
latest_values as (
    select
        indicator_code,
        indicator_label,
        value     as latest_value,
        year      as latest_year
    from source
    qualify row_number() over (
        partition by indicator_code
        order by year desc
    ) = 1
),

-- All years for time-series charts
time_series as (
    select
        indicator_code,
        indicator_label,
        year,
        round(value, 4) as value
    from source
),

-- Wide table of latest values per indicator
wide as (
    select
        max(case when indicator_code = 'SH.MED.BEDS.ZS'  then latest_value end) as hospital_beds_per_1000,
        max(case when indicator_code = 'SH.MED.BEDS.ZS'  then latest_year  end) as hospital_beds_year,
        max(case when indicator_code = 'SH.MED.PHYS.ZS'  then latest_value end) as physicians_per_1000,
        max(case when indicator_code = 'SH.MED.PHYS.ZS'  then latest_year  end) as physicians_year,
        max(case when indicator_code = 'SH.DYN.MORT'      then latest_value end) as under5_mortality_rate,
        max(case when indicator_code = 'SH.DYN.MORT'      then latest_year  end) as under5_mortality_year,
        max(case when indicator_code = 'SH.STA.MMRT'      then latest_value end) as maternal_mortality_ratio,
        max(case when indicator_code = 'SH.STA.MMRT'      then latest_year  end) as maternal_mortality_year,
        max(case when indicator_code = 'SP.POP.TOTL'      then latest_value end) as total_population,
        max(case when indicator_code = 'SP.POP.TOTL'      then latest_year  end) as population_year
    from latest_values
)

select * from wide
