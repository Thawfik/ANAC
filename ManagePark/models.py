
import uuid

from django.db import models


# STATUT DU VOL
VOL_STATUT_CHOICES = [
    ('ATTENTE', 'En Attente d\'Allocation'),
    ('ALLOUE', 'Alloué à un Stand'),
]

# STATUT DE L'INCIDENT
INCIDENT_STATUT_CHOICES = [
    ('OUVERT', 'Ouvert'),
    ('ENCOURS', 'En Cours de Résolution'),
    ('RESOLU', 'Résolu'),
]



class Avion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    immatriculation = models.CharField(max_length=5, unique=True, verbose_name="Immatriculation")

    # Dimensions
    longueur = models.DecimalField(max_digits=5, decimal_places=2, verbose_name="Longueur (m)")
    largeur = models.DecimalField(max_digits=5, decimal_places=2, verbose_name="Envergure/Largeur (m)")

    # Identification
    type = models.CharField(max_length=4, verbose_name="Type (Ex: B737)")
    description = models.CharField(max_length=255,
                                   verbose_name="Description de l'avion")

    def __str__(self):
        return f"{self.immatriculation} ({self.type})"


class Stand(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    nom_operationnel = models.CharField(max_length=10, unique=True)
    longueur = models.DecimalField(max_digits=5, decimal_places=2)
    largeur = models.DecimalField(max_digits=5, decimal_places=2)
    distance_stand_aerogare = models.IntegerField()  # Pour l'optimisation

    # Indique si le stand est physiquement opérationnel (pas en maintenance manuelle)
    disponibilite = models.BooleanField(default=True)

    def __str__(self):
        return self.nom_operationnel

    @property
    def statut_operationnel(self):
        """Détermine le statut opérationnel principal selon la nouvelle logique."""
        # 1. HORS SERVICE (Maintenance/Incident)
        # Assumant que 'disponibilite' existe et que 'incidents_rapportes' est la relation inverse vers Incident
        if not self.disponibilite or self.incidents_rapportes.filter(statut__in=['OUVERT', 'ENCOURS']).exists():
            return 'HORS_SERVICE'

        # 2. OCCUPE (Allocation en cours)
        if self.vol_occupant_actuel:
            return 'OCCUPE'

        # 3. LIBRE
        return 'LIBRE'

    @property
    def vol_occupant_actuel(self):
        """
        Recherche le seul vol dont la période d'occupation chevauche l'heure actuelle,
        et qui est alloué à ce stand.
        (La logique d'allocation est censée garantir l'unicité du chevauchement.)
        """
        from django.utils import timezone
        now = timezone.now()

        # On filtre les vols alloués à CE stand dont la période d'occupation est active
        # et on prend le premier résultat (car il ne devrait y en avoir qu'un seul)
        return self.vols_alloues.filter(
            date_heure_debut_occupation__lte=now,  # Débuté
            date_heure_fin_occupation__gt=now,
            statut='ALLOUE'# Non terminé
        ).first()  # Utiliser .first() est sûr même si l'unicité est violée (ce qui ne devrait pas arriver)



class Vol(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    num_vol_arrive = models.CharField(max_length=10, unique=True, verbose_name="Numéro de Vol Arrivée")
    num_vol_depart = models.CharField(max_length=10, blank=True, null=True,
                                      verbose_name="Numéro de Vol Départ")


    date_heure_debut_occupation = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Heure d'arrivée"
    )

    date_heure_fin_occupation = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Heure de départ"
    )

    provenance = models.CharField(max_length=100, verbose_name="Ville/Aéroport de Provenance")
    destination = models.CharField(max_length=100, verbose_name="Ville/Aéroport de Destination")

    # Relation avec l'Avion
    avion = models.ForeignKey(Avion, on_delete=models.SET_NULL, null=True, blank=True, related_name='vols_effectues',
                              verbose_name="Avion du vol")

    statut = models.CharField(max_length=10, choices=VOL_STATUT_CHOICES, default='ATTENTE',
                              verbose_name="Statut d'Allocation")

    stand_alloue = models.ForeignKey(Stand, on_delete=models.SET_NULL, null=True, blank=True, related_name='vols_alloues')





class Incident(models.Model):
    stand = models.ForeignKey(Stand, on_delete=models.CASCADE, related_name='incidents_rapportes',
                              verbose_name="Stand Affecté")
    description = models.TextField(verbose_name="Description de l'Incident")
    type_incident = models.CharField(max_length=50, verbose_name="Type (Ex: Panne Électrique)")

    # Dates de gestion
    date_heure_declaration = models.DateTimeField(auto_now_add=True, verbose_name="Date/Heure de Déclaration")
    date_heure_resolution = models.DateTimeField(null=True, blank=True, verbose_name="Date/Heure de Résolution")

    statut = models.CharField(max_length=10, choices=INCIDENT_STATUT_CHOICES, default='OUVERT', verbose_name="Statut")

    def __str__(self):
        return f"Incident sur {self.stand.nom_operationnel} - {self.get_statut_display()}"