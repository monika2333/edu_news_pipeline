-- migrate:up
INSERT INTO public.app_settings (section, value, version)
VALUES (
    'review_sort_keywords',
    '{"市教委":["市教委","市教委教育工委","教工委","教育工委","教育委员","首都教育两委","教育两委"],"中小学":["中小学","小学","初中","高中","义务教育","基础教育","幼儿园","幼儿","托育","k12","班主任","青少年","少儿","少年"],"高校":["高校","大学","学院","本科","研究生","硕士","博士"]}'::jsonb,
    1
);

-- migrate:down
DELETE FROM public.app_settings WHERE section = 'review_sort_keywords';
