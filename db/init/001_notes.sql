-- Example table showing how schema files are applied. Keep, replace or remove it.
create table public.notes (
  id bigserial primary key,
  title text not null
);

insert into public.notes (title)
values
  ('Today I started a Postgres container.'),
  ('I added some data and queried it from FastAPI.'),
  ('It was awesome!');
