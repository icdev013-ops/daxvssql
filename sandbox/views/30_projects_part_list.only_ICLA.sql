-- 30_projects_part_list.only_ICLA.sql: table that exists in the ICLA model only.

-- Projects Part List: closet-parts bill of materials, one row per part line per project (current list, not history).
-- M: icall_gold.icla_project_parts_list, entity filter. Derived fields are generated columns in MySQL.
CREATE OR REPLACE SQL SECURITY DEFINER VIEW `{SCHEMA}`.`Projects Part List` AS
SELECT
  `id`,
  `entity`,
  `project_name`,
  `category`,
  `original_item_id`,
  `original_description`,
  `quantity`,
  `sub_type`,
  `grain_type`,
  `dimensions`,
  `profile`,
  `color`,
  `component_type`,
  `component_code`,
  `width_inches`,
  `height_inches`,
  `depth_fraction`,
  `color_code`,
  `color_name`,
  `panel_type`,
  `panel_orientation`,
  `material`,
  `pack_size`,
  `is_adjustable`,
  `is_fixed`,
  `has_soft_close`,
  `source_created_at`,
  `data_ingested_at`
FROM icall_gold.icla_project_parts_list
WHERE `entity` = '{ENT}';
