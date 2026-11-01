# Payment Service

Microservice de paiement d’exemple construit avec FastAPI selon une architecture hexagonale.

## Lancement et tests

Avec Python 3.11 ou supérieur :

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -e ".[dev]"
uvicorn payment_service.adapters.inbound.api:app --reload --app-dir src --no-access-log
```

`--no-access-log` évite le doublon avec le log HTTP du middleware, qui porte déjà le `traceId` et la durée.

Dans un autre terminal, exécutez `pytest` pour lancer les tests. Les tests du cas d’utilisation couvrent acceptation, refus, timeout, exception, logs, reprise et panne du repository ; les tests API couvrent les réponses HTTP, la visibilité des paiements `PENDING` et la protection du mécanisme de reprise. `tests/test_tracing.py` vérifie la génération et la propagation du `traceId`, le format JSON et la chaîne `OrderCreated → PaymentConfirmed`, y compris après une panne ; `tests/test_sqlite_repository.py` vérifie l’interchangeabilité des deux repositories.  
  
## Swagger UI est disponible à `/docs`.
Le code HTTP varie selon l’acceptation ; le corps d’un refus expose le paiement enregistré.  
  
Pour tester manuellement, exemple HTTP :

```powershell
$body = @{ orderId = 'ORD-123'; amount = 49.99; currency = 'EUR' } | ConvertTo-Json -Compress
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/payments' -Method Post -ContentType 'application/json' -Body $body
```

## Architecture

Le domaine (`Payment`) reste indépendant de FastAPI et des adapters. Le port IN `PayOrderPort` expose les opérations applicatives ; `PayOrderUseCase` valide, demande le paiement, enregistre le résultat puis publie un événement. Il utilise les ports OUT `PaymentRepository`, `PaymentGateway` et `EventPublisher`. Le contrôleur REST et le webhook d’événement sont des adapters IN ; le stockage (mémoire ou SQLite), la passerelle simulée et le publisher mémoire sont les adapters OUT.

```text
HTTP client -> TraceIdMiddleware -> REST adapter -> PayOrderPort -> PayOrderUseCase
                (traceId)                                               |
                                                                        +-> PaymentRepository <- InMemoryPaymentRepository | SqlitePaymentRepository
                                                                        +-> PaymentGateway    <- MockPaymentGateway
                                                                        +-> EventPublisher    <- InMemoryEventPublisher
OrderCreated -> TraceIdMiddleware -> async webhook -> PayOrderPort
```

Les interfaces sont définies dans `ports/`. Une implémentation de base de données ou de prestataire externe peut les remplacer sans modifier le domaine ni le cas d’utilisation : c’est le cas de `SqlitePaymentRepository`, qui remplace `InMemoryPaymentRepository` par simple configuration.

La traçabilité (`observability/`) est transversale : elle ne dépend ni du domaine ni de FastAPI. Le middleware HTTP crée le `traceId` à l’entrée ; le cas d’utilisation l’attache à son contexte d’exécution, de sorte que tous les logs (use case et adapters) et tous les événements publiés portent le même identifiant.

## Arborescence

```text
src/payment_service/
  domain/payment.py
  application/pay_order.py
  application/errors.py
  ports/inbound.py
  ports/outbound.py
  domain/events.py
  adapters/inbound/api.py
  adapters/inbound/trace_middleware.py
  adapters/outbound/memory_repository.py
  adapters/outbound/sqlite_repository.py
  adapters/outbound/mock_gateway.py
  adapters/outbound/memory_event_publisher.py
  observability/trace.py
  observability/json_logging.py
tests/test_pay_order.py
tests/test_api.py
tests/test_tracing.py
tests/test_sqlite_repository.py
demo.ps1
pyproject.toml
```

## Contrat HTTP

`POST /payments` crée et traite un paiement. Il accepte `application/json` :

```json
{"orderId":"ORD-123","amount":49.99,"currency":"EUR"}
```

Les trois champs sont requis. `orderId` doit être non vide (1 à 100 caractères), `amount` doit être positif et avoir au plus deux décimales, `currency` doit être un code de trois lettres majuscules. Les propriétés supplémentaires sont refusées. Aucun numéro de carte n’est reçu ou conservé.

Paiement accepté : HTTP `200` et corps :

```json
{"paymentId":"PAY-…","orderId":"ORD-123","amount":"49.99","currency":"EUR","status":"PAID"}
```

Paiement refusé : HTTP `402`, avec `detail.payment` contenant le paiement au statut `FAILED`. La simulation refuse les montants dont le reste de la division par 10 vaut `0.99` (par exemple `0.99`, `10.99` ou `40.99`) ; les autres sont acceptés, `49.99` compris.

Timeout ou exception technique du gateway : HTTP `202`, paiement au statut `PENDING`, avec `persisted: true` si l’enregistrement a réussi :

```json
{"payment":{"paymentId":"PAY-…","orderId":"ORD-123","amount":"49.99","currency":"EUR","status":"PENDING"},"message":"Confirmation pending","persisted":true}
```

Panne du repository lors de l’enregistrement : HTTP `503`, `persisted: false` et identifiant du paiement ; aucune erreur interne n’est renvoyée.

JSON mal formé ou champs absents/invalides : HTTP `422` avec les détails de validation FastAPI. Le paiement refusé reste enregistré.

`GET /payments` renvoie tous les paiements enregistrés, y compris ceux au statut `PENDING`. Si le repository est indisponible, la route renvoie HTTP `503` sans exposer d’erreur interne.

`GET /payments/{paymentId}` renvoie les détails d’un paiement enregistré avec HTTP `200`. Si l’identifiant n’existe pas, l’API renvoie HTTP `404` avec `{"detail":"Payment not found"}`. Avec l’adapter mémoire (par défaut), les paiements sont accessibles tant que le processus tourne ; avec l’adapter SQLite, ils survivent au redémarrage.

Toutes les routes acceptent l’en-tête optionnel `X-Trace-Id` ; sans lui, le service génère un `traceId`.

Après l’enregistrement, le service publie `PaymentConfirmed` ou `PaymentDeclined` selon le résultat. Les événements utilisent l’enveloppe `{id, type, occurredAt, traceId?, payload}`. Le `InMemoryEventPublisher` les conserve en mémoire pour la démonstration ; il peut être remplacé par un adapter de broker.

Le service consomme aussi `OrderCreated` sur `POST /events/order-created`. Le webhook valide l’enveloppe puis répond HTTP `202` et lance le paiement en tâche de fond. Exemple :

```json
{"id":"evt-order-1","type":"OrderCreated","occurredAt":"2026-10-13T09:00:00Z","traceId":"trace-123","payload":{"orderId":"ORD-123","amount":49.99,"currency":"EUR"}}
```

Cette tâche de fond intégrée à FastAPI est adaptée à la démonstration locale, mais ne fournit pas de file durable : en production, le webhook serait remplacé par un adapter de broker avec accusé de réception et reprise des messages.

## Pannes et reprise locale

Le simulateur du gateway est configuré au démarrage avec `PAYMENT_GATEWAY_SCENARIO` : `normal`, `declined`, `timeout` ou `exception`. Le repository peut simuler une indisponibilité avec `PAYMENT_REPOSITORY_FAIL=true`. En local, on peut activer le contrôle du simulateur avec `PAYMENT_ENABLE_SIMULATION=true` ; sa route interne exige aussi `PAYMENT_INTERNAL_TOKEN`. Cette route est désactivée par défaut.

En cas de timeout ou d’exception du gateway, le résultat est incertain : le service journalise l’incident, enregistre le paiement `PENDING` et répond HTTP `202` avec `persisted: true`. Il ne retente pas automatiquement le débit. Chaque tentative utilise le même `paymentId` comme clé d’idempotence ; `POST /internal/payments/{paymentId}/retry` permet une reprise manuelle seulement lorsque `PAYMENT_INTERNAL_TOKEN` est configuré et fourni dans `X-Internal-Token`. Le simulateur mémorise la réponse par clé d’idempotence.

Si le repository est indisponible pendant l’enregistrement, le service journalise l’échec et renvoie HTTP `503` avec `persisted: false` et le `paymentId` associé. Il ne prétend donc pas que le paiement est enregistré. Les routes de consultation renvoient également `503` tant que le repository reste indisponible.

Exemple de démonstration PowerShell :

```powershell
$env:PAYMENT_GATEWAY_SCENARIO = 'normal'
$env:PAYMENT_INTERNAL_TOKEN = 'local-secret'
$env:PAYMENT_ENABLE_SIMULATION = 'true'
uvicorn payment_service.adapters.inbound.api:app --reload --app-dir src
```

Dans un autre terminal, créer un paiement accepté :

```powershell
$body = @{ orderId = 'ORD-OK'; amount = 15.00; currency = 'EUR' } | ConvertTo-Json -Compress
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/payments' -Method Post -ContentType 'application/json' -Body $body
```

Activer le timeout avec le contrôle interne, puis créer un paiement. La réponse attendue est HTTP `202` et contient `status: PENDING`; les logs indiquent le timeout et l’identifiant du paiement. Consulter `GET /payments` pour retrouver son `paymentId`. Remettre le simulateur en fonctionnement normal et reprendre le paiement :

```powershell
$scenario = @{ scenario = 'timeout' } | ConvertTo-Json -Compress
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/internal/gateway-scenario' -Method Post -Headers @{ 'X-Internal-Token' = 'local-secret' } -ContentType 'application/json' -Body $scenario
$body = @{ orderId = 'ORD-TIMEOUT'; amount = 24.00; currency = 'EUR' } | ConvertTo-Json -Compress
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/payments' -Method Post -ContentType 'application/json' -Body $body
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/payments' -Method Get
$scenario = @{ scenario = 'normal' } | ConvertTo-Json -Compress
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/internal/gateway-scenario' -Method Post -Headers @{ 'X-Internal-Token' = 'local-secret' } -ContentType 'application/json' -Body $scenario
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/internal/payments/PAY-ID/retry' -Method Post -Headers @{ 'X-Internal-Token' = 'local-secret' }
```

Le statut doit passer à `PAID`. Le journal contient un message du type `Payment gateway timed out; payment PAY-… remains PENDING`. Avec le repository mémoire, arrêter le serveur efface les données ; avec SQLite, elles sont conservées.

## Adapter base de données (SQLite)

`SqlitePaymentRepository` implémente le même port `PaymentRepository` que `InMemoryPaymentRepository`. Le choix se fait au démarrage par la factory `create_repository_from_env()`, sans changer une ligne du cas d’utilisation :

| Variable | Valeurs | Défaut |
|---|---|---|
| `PAYMENT_REPOSITORY` | `memory` ou `sqlite` | `memory` |
| `PAYMENT_DB_PATH` | chemin du fichier SQLite | `payments.db` |
| `PAYMENT_REPOSITORY_FAIL` | `true` simule une base indisponible (les deux adapters) | `false` |

SQLite est fourni avec Python : aucune installation de serveur n’est nécessaire. Un `PgRepository` suivrait exactement le même modèle (mêmes trois méthodes, même table). Les tests `tests/test_sqlite_repository.py` exécutent les mêmes scénarios sur les deux adapters pour démontrer qu’ils sont interchangeables.

## Traçabilité : traceId et logs JSON

**Génération à l’entrée.** `TraceIdMiddleware` reçoit chaque requête HTTP : il reprend l’en-tête `X-Trace-Id` s’il est fourni, sinon il génère un UUID. Pour un événement `OrderCreated`, le `traceId` de l’enveloppe est prioritaire car il prolonge le flux du service émetteur. Appelé directement (test, CLI), le cas d’utilisation génère lui-même un `traceId` s’il n’en reçoit pas.

**Propagation.** Le `traceId` est stocké dans une `ContextVar` : il suit la requête, la tâche de fond et le thread qui l’exécute. Il est écrit dans chaque ligne de log et dans l’enveloppe des événements publiés (`PaymentConfirmed`, `PaymentDeclined`).

**Format.** Chaque log est un objet JSON sur une seule ligne, sur la sortie standard :

```json
{"timestamp": "2026-10-29T11:34:41.222Z", "level": "INFO", "service": "payment-service", "traceId": "demo-ok-a72a2192", "msg": "Payment saved", "paymentId": "PAY-65E9BF4C495D", "duration_ms": 7.68}
```

Champs toujours présents : `timestamp`, `level`, `service`, `traceId`, `msg`. `duration_ms` est ajouté pour les durées mesurées : requête HTTP, appel au prestataire, écriture en base, traitement complet. Les erreurs ajoutent `error` (type et message de l’exception).

**Corrélation.** `GET /logs?traceId=…` renvoie les logs en mémoire (2 000 dernières lignes) pour ce `traceId`, dans l’ordre chronologique ; sans `traceId`, la route renvoie tous les logs en mémoire. En production, cette route serait retirée au profit d’un outil de centralisation des logs.

Chaîne obtenue pour `OrderCreated → PaymentConfirmed` :

```text
OrderCreated event received
HTTP POST /events/order-created completed with 202   duration_ms
Payment requested
Payment gateway responded                            duration_ms
Payment saved                                        duration_ms
Event published: PaymentConfirmed                    (enveloppe complète avec traceId)
Payment processed                                    duration_ms
```

En cas de panne du prestataire, la même trace montre `Payment gateway timed out … remains PENDING` (niveau `ERROR`), puis, si la reprise est appelée avec le même `X-Trace-Id`, `Payment retry requested` → `Event published: PaymentConfirmed`. Si l’enregistrement échoue pendant le traitement d’un `OrderCreated`, l’erreur est journalisée (`OrderCreated processing failed…`) sous le `traceId` de l’événement, sans faire tomber le serveur.

## Démonstration complète

Terminal 1 : lancer le service avec SQLite et le simulateur de pannes.

```powershell
$env:PAYMENT_REPOSITORY = 'sqlite'
$env:PAYMENT_ENABLE_SIMULATION = 'true'
$env:PAYMENT_INTERNAL_TOKEN = 'local-secret'
uvicorn payment_service.adapters.inbound.api:app --app-dir src --no-access-log
```

Terminal 2 : lancer le scénario.

```powershell
.\demo.ps1
```

Le script enchaîne les trois scénarios et affiche, pour chacun, le résultat de `GET /logs?traceId=…` :

1. `OrderCreated` consommé → paiement → `PaymentConfirmed` publié : interaction externe, événement publié, logs corrélés ;
2. prestataire en timeout → paiement `PENDING`, sans crash → reprise avec le même `traceId` → `PaymentConfirmed` : panne simulée et comportement résilient ;
3. `POST /payments` direct sur le port IN, sans `X-Trace-Id` : le service génère le `traceId`, que le script retrouve dans `GET /logs`.

Le terminal 1 affiche en parallèle les mêmes lignes JSON, précédées des messages de démarrage d’uvicorn. Après redémarrage du serveur, `GET /payments` relit les paiements depuis `payments.db`.

Exemple de consultation après création :

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/payments/PAY-IDENTIFIANT' -Method Get
```

## Présentation

« Le cas d’utilisation orchestre le paiement via trois ports sortants : le repository, le prestataire et le publisher d’événements. Si le prestataire répond qu’il refuse le paiement, le statut est `FAILED`. Si le résultat est incertain à cause d’un timeout ou d’une panne, le service l’enregistre en `PENDING`, journalise l’incident et permet une reprise protégée avec la même clé d’idempotence. Une panne du repository produit une réponse `503` qui indique clairement que l’enregistrement n’est pas garanti. Chaque flux reçoit un `traceId` à l’entrée (REST ou événement), propagé dans tous les logs JSON et dans les événements publiés : `GET /logs?traceId=…` reconstitue le parcours complet, de `OrderCreated` à `PaymentConfirmed`, panne et reprise comprises. »
