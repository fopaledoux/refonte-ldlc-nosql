#!/bin/sh
# =============================================================================
# Lot 3 — Mesures : débit et latence (cassandra-stress), taille sur disque,
# taille des partitions, projection au volume réel.
#
#   docker compose exec cassandra sh /lot3/queries/03-mesures.sh
#
# Environ 2 minutes. Les chiffres dépendent de la machine : un seul nœud Docker,
# tas Java de 768 Mo, partagé avec le reste du poste.
# =============================================================================
set -e
titre() {
  echo
  echo "=============================================================================="
  echo "$1"
  [ -n "$2" ] && echo "   $2"
  echo "=============================================================================="
}
# cassandra-stress : tas réduit, et seules les lignes de résultat utiles
STRESS="/opt/cassandra/tools/bin/cassandra-stress"
export JVM_OPTS="-Xms64m -Xmx384m"
resultats() { grep -E "^(Op rate|Latency mean|Latency median|Latency 95th|Latency 99th |Latency max|Total errors)" | sed 's/^/   /'; }

titre "M1. Écriture d'événements : 1 événement par requête, cohérence LOCAL_ONE" \
      "cassandra-stress, profil stress/evenements.yaml (table identique à events_by_hour)"
cqlsh -e "DROP KEYSPACE IF EXISTS ldlc_stress" > /dev/null
echo "> Latence à faible charge, 4 clients :"
echo "> cassandra-stress user profile=/lot3/stress/evenements.yaml 'ops(insert=1)' n=20000 cl=LOCAL_ONE -rate threads=4"
$STRESS user profile=/lot3/stress/evenements.yaml "ops(insert=1)" n=20000 cl=LOCAL_ONE \
  -rate threads=4 -node 127.0.0.1 -log level=minimal 2>&1 | resultats
echo
echo "> Débit, 32 clients :"
echo "> cassandra-stress user profile=/lot3/stress/evenements.yaml 'ops(insert=1)' n=100000 cl=LOCAL_ONE -rate threads=32"
$STRESS user profile=/lot3/stress/evenements.yaml "ops(insert=1)" n=100000 cl=LOCAL_ONE \
  -rate threads=32 -node 127.0.0.1 -log level=minimal 2>&1 | resultats

titre "M2. Lecture d'une partition (Q6) pendant que l'écriture continue" \
      "mélange 3 écritures pour 1 lecture, 16 clients, 30 secondes"
echo "> cassandra-stress user profile=/lot3/stress/evenements.yaml 'ops(insert=3,heure=1)' duration=30s cl=LOCAL_ONE -rate threads=16"
$STRESS user profile=/lot3/stress/evenements.yaml "ops(insert=3,heure=1)" duration=30s cl=LOCAL_ONE \
  -rate threads=16 -node 127.0.0.1 -log level=minimal 2>&1 | grep -E "^(Op rate|Latency mean|Latency median|Latency 95th|Latency 99th |Total errors)" | sed 's/^/   /'
cqlsh -e "DROP KEYSPACE ldlc_stress" > /dev/null
nodetool clearsnapshot --all -- ldlc_stress > /dev/null 2>&1 || true

titre "M3. Place sur disque et taille des partitions (après flush)" \
      "nodetool tablestats : taille compressée, ratio de compression, partitions"
nodetool flush ldlc_analytics
nodetool tablestats -F json ldlc_analytics > /tmp/stats.json
python3 - <<'EOF'
import json
stats = json.load(open("/tmp/stats.json"))["ldlc_analytics"]["tables"]
print(f"   {'table':<26} {'disque':>9} {'compr.':>7} {'partitions':>11} {'part. moy.':>11} {'part. max':>10}")
for nom in sorted(stats):
    t = stats[nom]
    ratio = t.get("sstable_compression_ratio", 0) or 0
    print(f"   {nom:<26} {int(t['space_used_live']) / 1e6:>7.2f} Mo {ratio:>7.2f} "
          f"{int(t['number_of_partitions_estimate']):>11} {int(t['compacted_partition_mean_bytes']):>9} o "
          f"{int(t['compacted_partition_maximum_bytes']):>8} o")
EOF

titre "M4. Projection au volume visé : 5 millions d'événements par jour" \
      "à partir des tailles mesurées ci-dessus (compressées, sur disque)"
python3 - <<'EOF'
import json, sys
sys.path.insert(0, "/lot3/lib")
import commun as C
from datetime import datetime, timedelta, timezone
stats = json.load(open("/tmp/stats.json"))["ldlc_analytics"]["tables"]
_, s = C.connecter()
# Nombre d'événements : partitions énumérées (7 jours x 24 heures, IN sur les 16 buckets), sans balayer la table
jour = datetime.now(timezone.utc).date()
ps = s.prepare("SELECT count(*) AS n FROM events_by_hour WHERE day = ? AND hour = ? AND bucket IN ?")
n = sum(s.execute(ps, (jour - timedelta(days=d), h, list(range(C.BUCKETS)))).one().n
        for d in range(8) for h in range(24))
brut = int(stats["events_by_hour"]["space_used_live"]) + int(stats["events_by_visit"]["space_used_live"])
par_evt = brut / n
jour_go = 5e6 * par_evt / 1e9
print(f"   événements chargés            : {n}")
print(f"   octets par événement          : {par_evt:.0f} o sur disque, journal + parcours (2 tables)")
print(f"   par jour (5 M d'événements)   : {jour_go:.1f} Go")
print(f"   90 jours de rétention (TTL)   : {jour_go * 90:.0f} Go, x 3 réplicas = {jour_go * 270 / 1000:.2f} To")
lignes_part = 1.5e6 / C.BUCKETS
moy = int(stats["events_by_hour"]["compacted_partition_mean_bytes"])
rows_par_part = n / max(int(stats["events_by_hour"]["number_of_partitions_estimate"]), 1)
print(f"   heure de pointe d'une sortie  : 1 500 000 événements / 16 buckets = {int(lignes_part)} lignes par partition")
print(f"   taille de cette partition     : environ {lignes_part * moy / rows_par_part / 1e6:.0f} Mo "
      f"({moy / rows_par_part:.0f} o par ligne, non compressée) ; sans bucket : {1.5e6 * moy / rows_par_part / 1e6:.0f} Mo")
EOF
