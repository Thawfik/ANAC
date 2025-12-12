# Fichier : services/allocationService.py
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import Vol, Stand


def allouer_stands_optimise(vols_a_traiter=None):
    """
    Tente d'allouer les stands aux vols en attente (statut='ATTENTE').
    
    LOGIQUE D'ALLOCATION:
    1. Priorité aux stands de dimensions EXACTES (longueur ET largeur égales)
    2. Si aucun stand exact, prendre le plus petit stand compatible
    3. Favoriser les stands proches de l'aérogare (distance_stand_aerogare)
    
    NOTE: Le statut_operationnel du Stand est calculé automatiquement via @property,
    donc on ne le modifie PAS manuellement.

    :param vols_a_traiter: QuerySet de vols à traiter spécifiquement, ou None pour tous les vols 'ATTENTE'.
    :return: Tuple (nombre de vols alloués, nombre de vols non alloués)
    """

    # 1. Identifier les vols à traiter
    if vols_a_traiter is None:
        vols = Vol.objects.filter(statut='ATTENTE').select_related('avion').order_by('date_heure_debut_occupation')
    else:
        vols = vols_a_traiter.filter(statut='ATTENTE').select_related('avion').order_by('date_heure_debut_occupation')

    # 2. Récupérer les stands actifs (disponibles et sans incident)
    stands_actifs = Stand.objects.filter(
        disponibilite=True
    ).exclude(
        # Exclure les stands hors service à cause d'incidents actifs
        incidents_rapportes__statut__in=['OUVERT', 'ENCOURS']
    ).order_by('distance_stand_aerogare')  # Optimisation: favoriser les stands proches

    allocated_count = 0
    unallocated_count = 0

    for vol in vols:
        # Vérification 1: Données d'occupation complètes
        dt_debut = vol.date_heure_debut_occupation
        dt_fin = vol.date_heure_fin_occupation

        if not (vol.avion and dt_debut and dt_fin):
            print(f"⚠️ Vol {vol.num_vol_arrive} ignoré : Avion ou période d'occupation manquant.")
            unallocated_count += 1
            continue

        best_stand = None
        stand_exact = None  # Pour stocker un stand aux dimensions exactes
        stand_compatible = None  # Pour stocker le plus petit stand compatible

        # 2. Parcourir les stands éligibles
        for stand in stands_actifs:
            
            # ✅ CORRECTION: Vérifier si le stand est COMPATIBLE (dimensions >= avion)
            # Un stand est compatible si ses dimensions sont >= aux dimensions de l'avion
            if not (vol.avion.longueur <= stand.longueur and vol.avion.largeur <= stand.largeur):
                continue  # Ce stand est trop petit, passer au suivant

            # Vérification B: Conflit temporel
            # Un conflit existe si un vol alloué chevauche la période [dt_debut, dt_fin]
            conflict_exists = stand.vols_alloues.filter(
                statut='ALLOUE',
            ).exclude(
                # Exclure les vols qui sont terminés avant le nouveau vol OU qui commencent après
                Q(date_heure_fin_occupation__lte=dt_debut) | Q(date_heure_debut_occupation__gte=dt_fin)
            ).exists()

            if conflict_exists:
                continue  # Ce stand a un conflit temporel, passer au suivant

            # ✅ Le stand est compatible dimensionnellement ET temporellement
            
            # Priorité 1 : Chercher un stand aux dimensions EXACTES
            if stand.longueur == vol.avion.longueur and stand.largeur == vol.avion.largeur:
                stand_exact = stand
                break  # On a trouvé le stand parfait, pas besoin de chercher plus loin
            
            # Priorité 2 : Stocker le plus petit stand compatible
            if stand_compatible is None:
                stand_compatible = stand
            else:
                # Comparer la surface pour trouver le plus petit
                surface_actuelle = stand.longueur * stand.largeur
                surface_stockee = stand_compatible.longueur * stand_compatible.largeur
                if surface_actuelle < surface_stockee:
                    stand_compatible = stand

        # 3. Choisir le meilleur stand (exact > compatible)
        if stand_exact:
            best_stand = stand_exact
        elif stand_compatible:
            best_stand = stand_compatible

        # 4. Allouer et enregistrer
        if best_stand:
            try:
                with transaction.atomic():
                    # Allouer le vol au stand
                    vol.stand_alloue = best_stand
                    vol.statut = 'ALLOUE'
                    vol.save()
                    
                    # ✅ PAS BESOIN de modifier statut_operationnel manuellement
                    # Il sera recalculé automatiquement via @property quand on accède à stand.statut_operationnel
                    
                    print(f"✅ Vol {vol.num_vol_arrive} alloué au stand {best_stand.nom_operationnel}")
                    allocated_count += 1
            except Exception as e:
                print(f"❌ Erreur lors de l'allocation du Vol {vol.num_vol_arrive}: {e}")
                unallocated_count += 1
        else:
            print(f"⚠️ Aucun stand compatible trouvé pour Vol {vol.num_vol_arrive} "
                  f"(Avion: {vol.avion.longueur}x{vol.avion.largeur})")
            unallocated_count += 1

    return allocated_count, unallocated_count


@transaction.atomic
def reallouer_vol_unique(vol_pk: int) -> tuple[bool, str]:
    """
    Service pour forcer la réallocation d'un seul vol suite à un incident sur son stand.
    
    NOTE: Le statut_operationnel du Stand est calculé automatiquement via @property,
    donc on ne le modifie PAS manuellement.
    """
    try:
        vol = Vol.objects.get(pk=vol_pk)
    except Vol.DoesNotExist:
        return False, "Erreur : Vol introuvable."

    if vol.statut != 'ALLOUE':
        return False, f"Vol {vol.num_vol_arrive} n'est pas alloué. Action annulée."

    # Sauvegarder l'ancien stand
    old_stand = vol.stand_alloue
    
    if not old_stand:
        return False, f"Vol {vol.num_vol_arrive} n'a pas de stand alloué."

    # Vérification : l'incident doit être actif
    incident_actif = old_stand.incidents_rapportes.filter(statut__in=['OUVERT', 'ENCOURS']).exists()
    if not incident_actif:
        return False, f"Le Stand {old_stand.nom_operationnel} n'a plus d'incident actif. Réallocation annulée."

    # --- DÉBUT DE LA RÉALLOCATION ---

    # 1. Libérer le vol (pas besoin de toucher au stand, son statut sera recalculé automatiquement)
    vol.statut = 'ATTENTE'
    vol.stand_alloue = None
    vol.save()

    # 2. Tenter une nouvelle allocation
    allocated, _ = allouer_stands_optimise(Vol.objects.filter(pk=vol_pk))

    if allocated > 0:
        # Recharger le vol pour obtenir le nouveau stand
        vol.refresh_from_db()
        new_stand = vol.stand_alloue
        return True, f"Vol {vol.num_vol_arrive} réalloué de **{old_stand.nom_operationnel}** à **{new_stand.nom_operationnel}**."
    else:
        # Échec de la réallocation
        return False, f"Échec de la réallocation. Vol {vol.num_vol_arrive} mis en file d'attente (ATTENTE). Aucune alternative trouvée."


def liberer_stands_termines():
    """
    Service périodique (à appeler via une tâche CRON ou Celery) pour libérer
    automatiquement les stands dont les vols sont terminés.
    
    NOTE: Avec statut_operationnel en @property, cette fonction n'est plus nécessaire
    pour libérer les stands (ils se libèrent automatiquement). Elle sert juste à
    marquer les vols comme 'TERMINE'.
    
    :return: Nombre de vols marqués comme terminés
    """
    now = timezone.now()
    
    # Trouver tous les vols alloués dont la fin d'occupation est passée
    vols_termines = Vol.objects.filter(
        statut='ALLOUE',
        date_heure_fin_occupation__lte=now
    )
    
    vols_count = 0
    
    for vol in vols_termines:
        # Marquer le vol comme terminé
        vol.statut = 'TERMINE'
        vol.save()
        vols_count += 1
        print(f"✅ Vol {vol.num_vol_arrive} marqué comme TERMINE")
    
    return vols_count