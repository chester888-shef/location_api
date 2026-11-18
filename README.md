# Мапа популярності локацій

Готові етапи 1–4 та Swagger: користувачі, Session/Cookies і CSRF, скидання
пароля, категорії й локації, Soft Delete, відгуки, Like/Dislike та `CacheRevision`.
Перегляди й рейтинг/популярність — етап 5; кеш списку, пагінація й експорт — етап 6.

## Локальний запуск

1. Перший запуск: створи `.env` із `.env.example`, задай власні `SECRET_KEY`
   і `POSTGRES_PASSWORD`. Наявний `.env` збережи.
2. Запусти або онови сервер:

   ```bash
   docker compose up --build -d
   ```

3. Відкрий [Swagger](http://localhost:8000/api/docs/). `/` веде туди ж.
   Міграції виконуються автоматично; для JS/CSS Swagger потрібен інтернет.

## Початок перевірки у Swagger

Використовуй **Try it out → Execute**. CSRF і session cookie передаються автоматично.

1. `GET /api/auth/csrf/` → **200**.
2. Створи два акаунти через `POST /api/auth/register/` → **201**; дубль email → **400**.
3. Увійди через `POST /api/auth/login/` → **200**, вихід — `logout` → **204**.
4. Створи категорію (`{"name":"Парки"}`), потім локацію за прикладом у Swagger
   → **201**. У `category` підстав ID створеної категорії.

Superuser для перевірки адміністративних прав:

```bash
docker compose exec web python manage.py createsuperuser
```

Увійди ним через звичайний `POST /api/auth/login/`.

## Категорії та локації

- GET списків/деталей — також для гостя; створення — після входу.
- Назви категорій унікальні без регістру. Зміна/видалення категорії — лише
  superuser, іншим **403**. Її видалення зберігає локацію з `category=null`;
  для наступного редагування признач нову категорію.
- Авторський PATCH локації → **200**, чужий PATCH/DELETE → **403**.
  POST/PUT вимагають усіх шести полів; невалідні координати або категорія → **400**.
- DELETE локації → **204**. Вона зникає зі звичайного списку, GET → **404**.
  Superuser може переглядати її з `is_deleted=true`; зміни видаленого заборонені.

## Етап 4 — відгуки та голоси

Перевіряй на активній локації; ID бери з відповідей API.

1. Перший акаунт: `POST /api/locations/{location_id}/reviews/` з
   `{"rating":4,"comment":"Гарне місце."}` → **201**. Повторний відгук → **409**;
   рейтинг поза 1–5, дробовий рейтинг або порожній коментар → **400**.
2. `GET /api/locations/{location_id}/reviews/` — публічний список (**200**).
3. Автор відгуку: `PATCH /api/reviews/{id}/` з `{"rating":5}` → **200**.
   Інший користувач, зокрема власник локації, → **403**; superuser може змінювати.
4. Другий акаунт: `PUT /api/reviews/{id}/vote/` з `{"value":"like"}` → **200**.
   Повтори із `dislike` → **200**, ID голосу той самий.
   `DELETE` цього маршруту → **204**; повторне скасування теж **204**.
5. Автор відгуку не може голосувати за нього: **403**, навіть для superuser.
   Автор і користувач голосу задаються сервером.
6. Автор відгуку або superuser: `DELETE /api/reviews/{id}/` → **204**;
   відгук і його голоси фізично видаляються.
7. Створи відгук знову та виконай Soft Delete локації. Її відгуки повертають
   **404** для звичайних користувачів; superuser може читати список.
   Створення/зміна відгуків і голосування під видаленою локацією заборонені.

Номер ревізії можна прочитати до та після зміни:

```bash
docker compose exec web python manage.py shell -c 'from core.models import CacheRevision; print(CacheRevision.objects.get(name="locations").version)'
```

Він збільшується після створення/зміни/Soft Delete локації, зміни/видалення
категорії, створення/зміни/видалення відгуку. Голоси, створення категорії та
відхилені запити його не змінюють. Сам Redis-кеш списку буде на етапі 6.

## Скидання пароля

`POST /api/auth/password-reset/` з email → **200**. За console backend знайди лист:

```bash
docker compose logs --tail 80 web
```

Передай `uid`, `token`, `new_password` у `POST /api/auth/password-reset/confirm/`
→ **200**; повтор токена й старий пароль → **400**. Новий пароль дозволяє увійти.
Клієнтської сторінки за посиланням із листа в MVP немає.

## Автоматичні перевірки

Після змін коду перебудуй образ. Перевірки етапу 4:

```bash
docker compose build web
docker compose run --rm web pytest tests/test_reviews.py tests/test_cache_revision.py tests/test_review_concurrency.py tests/test_docs.py -q
```

Усі тести та системні перевірки:

```bash
docker compose run --rm web pytest -q
docker compose run --rm web python manage.py check
docker compose run --rm web python manage.py makemigrations --check --dry-run
docker compose run --rm web python manage.py spectacular --validate --fail-on-warn --file /tmp/openapi.yaml
```

Очікується `passed`, код 0, відсутність нових міграцій і попереджень OpenAPI.
Для перевірки запуску з вимкненого стану виконай `docker compose down`, потім
команду тестів. Тести використовують окрему PostgreSQL-базу `test_...` і Redis DB 1;
робочі дані не очищаються, реальні листи не надсилаються. Зупинка залежностей —
`docker compose down` без `-v`.

Локально пошта виводиться в логи. Для SMTP налаштуй `EMAIL_BACKEND` та `EMAIL_*`;
`EMAIL_TIMEOUT=5`, гарантії доставки немає. Токен скидання діє до 3 діб і стає
недійсним після зміни пароля. Змінні shell мають пріоритет над `.env`;
`DEBUG` — `true/false` або `1/0`. Для HTTPS: `DEBUG=false`, відповідні
`ALLOWED_HOSTS`, `SESSION_COOKIE_SECURE=true`, `CSRF_COOKIE_SECURE=true`.
Секрети `.env` не додавай до системи контролю версій.
