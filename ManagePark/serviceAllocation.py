# Fichier : services/allocationService.py
from django.db import transaction
from django.db.models import Q
from django.utils import timezone


from .models import Vol, Stand  # Assurez-vous d'importer les modèles corrects




def allouer_stands_optimise(vols_a_traiter=None):
    """
    Tente d'allouer les stands aux vols en attente (statut='ATTENTE').

    :param vols_a_traiter: QuerySet de vols à traiter spécifiquement, ou None pour tous les vols 'ATTENTE'.
    :return: Tuple (nombre de vols alloués, nombre de vols non alloués)
    """

    # 1. Identifier les vols à traiter
    if vols_a_traiter is None:
        vols = Vol.objects.filter(statut='ATTENTE').select_related('avion').order_by('date_heure_debut_occupation')
    else:
        vols = vols_a_traiter.filter(statut='ATTENTE').select_related('avion').order_by('date_heure_debut_occupation')

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
            print(f"Alerte: Vol {vol.num_vol_arrive} ignoré car Avion ou Période d'occupation est manquant.")
            unallocated_count += 1
            continue

        best_stand = None

        # 2. Parcourir les stands éligibles
        for stand in stands_actifs:

            # Vérification A: Compatibilité dimensionnelle (inchangée)
            if vol.avion.longueur > stand.longueur or vol.avion.largeur > stand.largeur:
                continue

                # Vérification B: Conflit temporel (Requête directe en BDD)
            vols_sans_conflit = stand.vols_alloues.filter(
                statut='ALLOUE',
            ).filter(
                # Condition de NON-CHEVANCHEMENT : [DB_fin <= dt_debut] OU [DB_debut >= dt_fin]
                Q(date_heure_fin_occupation__lte=dt_debut) | Q(date_heure_debut_occupation__gte=dt_fin)
            )

            conflict_exists = stand.vols_alloues.filter(
                statut='ALLOUE',
            ).exclude(
                # Exclure les vols qui sont terminés avant le nouveau vol OU qui commencent après le nouveau vol
                Q(date_heure_fin_occupation__lte=dt_debut) | Q(date_heure_debut_occupation__gte=dt_fin)
            ).exists()

            if not conflict_exists:
                best_stand = stand
                break

        # 3. Allouer et enregistrer (Les champs d'occupation sont déjà renseignés dans le Vol)
        if best_stand:
            try:
                vol.stand_alloue = best_stand
                vol.statut = 'ALLOUE'
                vol.save()
                allocated_count += 1
            except Exception as e:
                print(f"Erreur lors de l'enregistrement de l'allocation pour Vol {vol.num_vol_arrive}: {e}")
                unallocated_count += 1
        else:
            unallocated_count += 1

    return allocated_count, unallocated_count


@transaction.atomic
def reallouer_vol_unique(vol_pk: int) -> tuple[bool, str]:
    """
    Service pour forcer la réallocation d'un seul vol suite à un incident sur son stand.
    """
    # ... (Début inchangé) ...
    try:
        vol = Vol.objects.get(pk=vol_pk)
    except Vol.DoesNotExist:
        return False, "Erreur : Vol introuvable."

    if vol.statut != 'ALLOUE':
        return False, f"Vol {vol.num_vol_arrive} n'est pas alloué. Action annulée."

    # 1. Trouver l'allocation active et l'ancien stand
    try:
        # CORRECTION ICI: Utilisation de la relation inversée
        allocation_active = vol.historique_allocations_vol.get(heure_fin__isnull=True)
    except Allocation_stand_vol.DoesNotExist:
        return False, f"Vol {vol.num_vol_arrive} est alloué mais aucune allocation active trouvée."
    except Allocation_stand_vol.MultipleObjectsReturned:
        return False, f"Erreur critique: Plusieurs allocations actives pour Vol {vol.num_vol_arrive}."  # Sécurité

    old_stand = allocation_active.stand

    # Vérification finale (sécurité) : l'incident doit toujours être là
    incident_actif = old_stand.incidents_rapportes.filter(statut__in=['OUVERT', 'ENCOURS']).exists()
    if not incident_actif:
        return False, f"Le Stand {old_stand.nom_operationnel} n'a plus d'incident actif. Réallocation annulée."

    # --- DÉBUT DE LA RÉALLOCATION ---

    # 2. Terminer l'ancienne allocation
    allocation_active.heure_fin = timezone.now()
    allocation_active.save()

    # 3. Libérer le vol pour qu'il soit éligible à l'allocation optimisée
    # Le statut doit être ATTENTE pour que allouer_stand_optimise le sélectionne.
    vol.statut = 'ATTENTE'
    vol.save()

    # 4. Tenter une nouvelle allocation
    succes, new_stand = allouer_stand_optimise(vol)  # allouer_stand_optimise retourne (succes, Stand | None)

    if succes:
        return True, f"Vol {vol.num_vol_arrive} réalloué de **{old_stand.nom_operationnel}** à **{new_stand.nom_operationnel}**."
    else:
        # Échec de la réallocation (aucun autre stand compatible disponible)
        # Le vol reste en ATTENTE
        return False, f"Échec de la réallocation. Vol {vol.num_vol_arrive} mis en file d'attente (ATTENTE). Aucune alternative trouvée."

