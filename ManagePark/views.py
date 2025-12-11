from django.core.exceptions import ObjectDoesNotExist
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.views import View
from django.views.generic import ListView, DetailView, UpdateView, DeleteView
from django.db.models import  Exists, OuterRef, F
from django.views.generic.edit import CreateView
from django.urls import reverse_lazy
from django.shortcuts import redirect
from django.contrib import messages
from django.db import transaction

from . import serviceAllocation
from .models import Vol, Avion, Stand, Incident
from .forms import StandForm, IncidentForm, VolUpdateForm, AvionForm
from .serviceAllocation import reallouer_vol_unique, allouer_stands_optimise


# =========================================================
# VUES VOLS-AVION
# =========================================================
class VolCreateView(CreateView):
    """
    Permet de créer un nouveau vol, avec création/sélection optionnelle d'un avion.
    """
    model = Vol
    # Champs du Vol que l'utilisateur doit remplir
    fields = [
        'num_vol_arrive', 'num_vol_depart', 'date_heure_debut_occupation',
        'date_heure_fin_occupation', 'provenance', 'destination'
    ]
    template_name = 'vols/vol_create.html'
    success_url = reverse_lazy('vol_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Création d'un formulaire pour l'avion (pour l'intégration dans le même template)
        if self.request.POST:
            context['avion_form'] = AvionForm(self.request.POST)
        else:
            context['avion_form'] = AvionForm()
        return context

    def form_valid(self, form):
        # Le formulaire du Vol est déjà validé si nous atteignons ce point.
        avion_form = AvionForm(self.request.POST)

        # 1. Valider le formulaire Avion
        if avion_form.is_valid():

            immatriculation = avion_form.cleaned_data['immatriculation']

            # Vérifier si l'avion existe (grâce à la logique clean_immatriculation)
            if avion_form.cleaned_data.get('est_existant'):
                # Récupération si l'avion existe
                avion_instance = Avion.objects.get(immatriculation=immatriculation)
            else:
                # Création si l'avion est nouveau (les champs sont remplis car la validation est passée)
                avion_instance = avion_form.save()

            # 2. Associer et Sauvegarder le Vol
            form.instance.avion = avion_instance
            form.instance.statut = 'ATTENTE'

            return super().form_valid(form)
        else:
            # 3. Échec si la validation de l'Avion n'a pas réussi (que ce soit pour une nouvelle création ou une mauvaise immat.)
            self.object = form.instance
            context = self.get_context_data()
            context['form'] = form
            context['avion_form'] = avion_form  # AvionForm avec erreurs pour affichage
            return self.render_to_response(context)


class VolListView(ListView):
    """Affiche tous les vols actifs (ATTENTE ou ALLOUE)."""
    model = Vol
    context_object_name = 'vols'
    template_name = 'vols/vol_list.html'
    ordering = ['heure_arrivee'] # Trier par ETA

    def get_queryset(self):
        # N'afficher que les vols actifs dans le système d'allocation
        return Vol.objects.filter(statut__in=['ATTENTE', 'ALLOUE']).select_related('avion')


class VolUpdateView(UpdateView):
    """Permet de modifier les détails d'un vol existant."""
    model = Vol
    # Utilise les mêmes champs d'entrée que la création, car ce sont les données modifiables.
    fields = [
        'num_vol_arrive', 'num_vol_depart', 'date_heure_debut_occupation',
        'date_heure_fin_occupation', 'provenance', 'destination',
        # Note: L'avion n'est pas modifiable ici pour simplifier.
    ]
    context_object_name = 'vol'
    template_name = 'vols/vol_create.html'  # Réutilise le même template de formulaire

    def get_success_url(self):
        # Redirige vers la page de détails du vol après modification
        messages.success(self.request, f"Le vol {self.object.num_vol_arrive} a été mis à jour.")
        return reverse_lazy('vol_detail', kwargs={'pk': self.object.pk})

    def form_valid(self, form):
        # Si les heures d'occupation sont modifiées, le statut doit redevenir 'ATTENTE'
        # pour forcer l'algorithme d'allocation à re-vérifier la disponibilité du stand.

        # NOTE IMPORTANTE : Ceci est une règle métier !
        if (form.cleaned_data['date_heure_debut_occupation'] != self.object.date_heure_debut_occupation or
                form.cleaned_data['date_heure_fin_occupation'] != self.object.date_heure_fin_occupation):
            form.instance.statut = 'ATTENTE'
            form.instance.stand_alloue = None
            messages.info(self.request,
                          "Les temps d'occupation ont été modifiés. Le vol est repassé en statut 'ATTENTE' pour réallocation.")

        return super().form_valid(form)


class VolDeleteView(DeleteView):
    """Permet de supprimer un vol."""
    model = Vol
    context_object_name = 'vol'
    template_name = 'vols/vol_confirm_delete.html'
    success_url = reverse_lazy('vol_list')

    def form_valid(self, form):
        # Ajout d'un message flash avant la suppression effective
        messages.success(self.request, f"Le vol {self.object.num_vol_arrive} a été supprimé.")
        return super().form_valid(form)


class VolDetailView(DetailView):
    """Affiche les détails d'un vol spécifique et son statut d'allocation."""
    model = Vol
    context_object_name = 'vol'
    template_name = 'vols/vol_detail.html'

    # Préfétcher l'Avion et le Stand pour éviter les requêtes inutiles dans le template
    def get_queryset(self):
        return Vol.objects.select_related('avion', 'stand_alloue')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Le vol est accessible via self.object ou context['vol']
        vol = self.object

        # Ajout d'une information utile : Est-ce que le vol est l'occupant actuel du stand ?
        if vol.stand_alloue and vol.statut == 'ALLOUE':
            # Utilise la propriété que nous avons définie sur le modèle Stand
            occupant_actuel = vol.stand_alloue.vol_occupant_actuel

            if occupant_actuel and occupant_actuel.pk == vol.pk:
                context['est_occupant_actuel'] = True
            else:
                context['est_occupant_actuel'] = False
        else:
            context['est_occupant_actuel'] = False

        return context

# =========================================================
# VUES STANDS
# =========================================================
class StandListView(ListView):
    """Liste tous les stands avec leurs informations de disponibilité."""
    model = Stand
    context_object_name = 'stands'
    template_name = 'stands/stand_list.html'

    # Pas de pré-fetching complexe nécessaire ici, mais tri utile
    def get_queryset(self):
        # Trier par nom opérationnel pour un affichage logique
        return Stand.objects.all().order_by('nom_operationnel')


class StandDetailView(DetailView):
    """
    Affiche les détails d'un stand spécifique, y compris les vols alloués
    et les incidents en cours.
    """
    model = Stand
    context_object_name = 'stand'
    template_name = 'stands/stand_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        stand = self.object

        # 1. Vols alloués : Afficher uniquement les vols futurs alloués à ce stand
        now = timezone.now()
        context['vols_futurs_alloues'] = stand.vol_set.filter(
            statut='ALLOUE',
            date_heure_fin_occupation__gt=now
        ).order_by('date_heure_debut_occupation')

        # 2. Incidents en cours/ouverts
        context['incidents_actifs'] = Incident.objects.filter(
            stand=stand,
            statut__in=['OUVERT', 'ENCOURS']
        ).order_by('-date_heure_declaration')

        # 3. Calcul de l'occupant actuel (logique métier)
        context['occupant_actuel'] = stand.vol_occupant_actuel  # Utilise la propriété du modèle

        return context


class StandCreateView(CreateView):
    """Permet de créer un nouveau stand."""
    model = Stand
    fields = ['nom_operationnel', 'longueur', 'largeur', 'distance_stand_aerogare']
    template_name = 'stands/stand_create.html'
    success_url = reverse_lazy('stand_list')

    def form_valid(self, form):
        # Assurer la disponibilité 'DISPONIBLE' par défaut à la création
        form.instance.disponibilite =True
        Stand.statut_operationnel = 'LIBRE'
        messages.success(self.request, f"Le stand {form.instance.nom_operationnel} a été créé.")
        return super().form_valid(form)


class StandUpdateView(UpdateView):
    """Permet de modifier les dimensions ou le statut opérationnel d'un stand."""
    model = Stand
    fields = ['nom_operationnel', 'longueur', 'largeur', 'statut_operationnel']
    context_object_name = 'stand'
    template_name = 'stands/stand_create.html'

    def get_success_url(self):
        messages.success(self.request, f"Le stand {self.object.nom_operationnel} a été mis à jour.")
        return reverse_lazy('stand_detail', kwargs={'pk': self.object.pk})

    def form_valid(self, form):
        # Logique métier: Si les dimensions sont modifiées, réallouer les vols affectés.
        # NOTE : Cela pourrait être très coûteux. On se contente d'afficher un message
        # pour l'instant, car la réallocation complète est complexe à gérer ici.
        # Idéalement, on marquerait les vols comme 'ATTENTE' si la nouvelle dimension est trop petite.

        if (form.cleaned_data['longueur'] != self.object.longueur or
                form.cleaned_data['largeur'] != self.object.largeur):
            messages.warning(self.request,
                             "Les dimensions du stand ont changé. Veuillez relancer l'algorithme d'allocation pour vérifier la validité des vols futurs.")

        return super().form_valid(form)


class StandDeleteView(DeleteView):
    """Permet de supprimer un stand."""
    model = Stand
    context_object_name = 'stand'
    template_name = 'stands/stand_confirm_delete.html'
    success_url = reverse_lazy('stand_list')

    def form_valid(self, form):
        # Vérification métier : Interdire la suppression si des vols futurs y sont alloués
        now = timezone.now()
        vols_futurs = self.object.vol_set.filter(
            statut='ALLOUE',
            date_heure_debut_occupation__gt=now
        )

        if vols_futurs.exists():
            messages.error(self.request,
                           f"Impossible de supprimer le stand {self.object.nom_operationnel}. {vols_futurs.count()} vol(s) futurs y sont encore alloués.")
            return redirect('stand_detail', pk=self.object.pk)

        messages.success(self.request, f"Le stand {self.object.nom_operationnel} a été supprimé.")
        return super().form_valid(form)


# =========================================================
# VUES INCIDENT
# =========================================================

def handle_incident_impact(stand_instance, request):
    """
    Récupère tous les vols alloués à ce stand qui n'ont pas encore commencé
    et les remet en statut 'ATTENTE'. Déclenche ensuite une réallocation.
    """
    now = timezone.now()

    # Récupérer les vols affectés : alloués à CE stand ET leur début d'occupation est DANS LE FUTUR
    affected_vols = Vol.objects.filter(
        stand_alloue=stand_instance,
        statut='ALLOUE',
        date_heure_debut_occupation__gt=now  # Le vol n'est pas encore arrivé
    )

    count = affected_vols.count()
    if count > 0:
        # Réinitialisation des statuts en masse pour une meilleure performance
        affected_vols.update(
            statut='ATTENTE',
            stand_alloue=None
        )

        messages.warning(request,
                         f"{count} vol(s) alloués au stand {stand_instance.nom_operationnel} ont été passés en 'ATTENTE' à cause de l'incident.")

        # 2. Déclenchement de la réallocation immédiate
        # On passe le QuerySet des vols affectés pour que le service ne traite qu'eux (optimisation)
        allocated, unallocated = allouer_stands_optimise(vols_a_traiter=affected_vols)

        if allocated > 0:
            messages.success(request, f"✅ {allocated} vol(s) ont été réalloués avec succès.")
        if unallocated > 0:
            messages.error(request,
                           f"❌ {unallocated} vol(s) n'ont pas pu être réalloués immédiatement après l'incident.")

    return count


class IncidentCreateView(CreateView):
    """
    Permet de déclarer un nouvel incident sur un Stand.
    Déclenche une réallocation si des vols futurs sont affectés.
    """
    model = Incident
    fields = ['stand', 'type_incident', 'description']
    template_name = 'incidents/incident_create.html'
    success_url = reverse_lazy('incident_list')

    def form_valid(self, form):
        # 1. Assurer que le statut est 'OUVERT' lors de la déclaration initiale
        form.instance.statut = 'OUVERT'

        # 2. Sauvegarde de l'incident
        response = super().form_valid(form)

        # 3. Vérification de l'impact et réallocation
        # Si un vol était alloué à ce stand, il est déclassé en 'ATTENTE'
        handle_incident_impact(form.instance.stand, self.request)

        messages.success(self.request, f"L'incident a été déclaré sur le stand {form.instance.stand.nom_operationnel}.")
        return response


class IncidentUpdateView(UpdateView):
    """
    Permet de modifier les détails d'un incident, y compris le changement de statut.
    Déclenche une réallocation si l'incident est réouvert.
    """
    model = Incident
    fields = ['stand', 'type_incident', 'description', 'statut']
    context_object_name = 'incident'
    template_name = 'incidents/incident_create.html'

    def get_success_url(self):
        messages.success(self.request, f"L'incident sur {self.object.stand.nom_operationnel} a été mis à jour.")
        return reverse_lazy('incident_detail', kwargs={'pk': self.object.pk})

    def form_valid(self, form):
        original_statut = self.object.statut  # Statut avant la modification
        new_statut = form.cleaned_data['statut']

        trigger_reallocation = False

        # Logique métier: Gérer l'heure de résolution
        if new_statut == 'RESOLU' and not form.instance.date_heure_resolution:
            form.instance.date_heure_resolution = timezone.now()
        elif new_statut != 'RESOLU':
            form.instance.date_heure_resolution = None  # Effacer l'heure si l'incident est réouvert/modifié

        # Détection du besoin de réallocation : Si le statut passe de RESOLU (Stand OK)
        # à OUVERT ou ENCOURS (Stand Bloqué), on doit réallouer.
        if original_statut == 'RESOLU' and new_statut in ['OUVERT', 'ENCOURS']:
            trigger_reallocation = True

        response = super().form_valid(form)

        # Exécution de l'impact si le stand est rebloqué
        if trigger_reallocation:
            handle_incident_impact(form.instance.stand, self.request)

        return response



class IncidentResolutionView(UpdateView):
    """Vue pour modifier et potentiellement résoudre un incident."""
    model = Incident
    # On ajoute la date de résolution et le statut au formulaire de modification
    fields = ['type_incident', 'description', 'statut', 'date_heure_resolution']
    template_name = 'incidents/incident_resolution.html'

    @transaction.atomic
    def form_valid(self, form):
        incident = form.save(commit=False)

        # Si le statut passe à 'RESOLU'
        if incident.statut == 'RESOLU':
            # Si la date de résolution n'est pas encore définie, la définir maintenant
            if incident.date_heure_resolution is None:
                incident.date_heure_resolution = timezone.now()

            # Tenter de rendre le stand disponible (seulement si AUCUN autre incident n'est ouvert)
            stand = incident.stand
            incidents_actifs_restants = stand.incidents_rapportes.filter(
                statut__in=['OUVERT', 'ENCOURS']
            ).exclude(pk=incident.pk)  # Exclure l'incident que nous sommes en train de résoudre

            if not incidents_actifs_restants.exists():
                stand.disponibilite = True
                stand.save()
                messages.success(self.request,
                                 f"L'incident a été résolu. Le Stand {stand.nom_operationnel} est de nouveau disponible pour l'allocation.")
            else:
                messages.warning(self.request,
                                 f"L'incident a été résolu, mais le Stand {stand.nom_operationnel} reste indisponible car {incidents_actifs_restants.count()} autre(s) incident(s) actif(s) persiste(nt).")

        incident.save()
        messages.info(self.request, f"Incident {incident.pk} mis à jour (Statut: {incident.get_statut_display()}).")

        return redirect('stand_detail', pk=incident.stand.pk)


# Pour lister tous les incidents du système (pas seulement ceux d'un stand)
class IncidentListView(ListView):
    model = Incident
    context_object_name = 'incidents'
    template_name = 'incidents/incident_list.html'
    ordering = ['-date_heure_declaration']



from django.views.generic import TemplateView


class DashboardView(TemplateView):
    """Vue principale affichant un résumé des stands, vols et incidents."""
    template_name = 'dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        now = timezone.now()

        # --- 1. Statistiques des Stands ---

        # Le statut_operationnel est déjà géré par la propriété du modèle
        total_stands = Stand.objects.count()
        stands_bloques = Stand.objects.filter(
            # Un stand est bloqué s'il a un incident actif
            incidents_rapportes__statut__in=['OUVERT', 'ENCOURS']
        ).distinct().count()

        # Stands actuellement occupés (via la propriété vol_occupant_actuel)
        stands_occupes = 0
        for stand in Stand.objects.all():
            if stand.vol_occupant_actuel:
                stands_occupes += 1

        stands_disponibles = total_stands - stands_bloques - stands_occupes

        context['stand_stats'] = {
            'total': total_stands,
            'occupes': stands_occupes,
            'bloques': stands_bloques,
            'disponibles': stands_disponibles,
        }

        # --- 2. Statistiques des Vols ---

        # Vols en attente d'allocation
        vols_attente = Vol.objects.filter(statut='ATTENTE').count()

        # Vols alloués et futurs (ne sont pas encore arrivés)
        vols_alloues_futurs = Vol.objects.filter(
            statut='ALLOUE',
            date_heure_debut_occupation__gt=now
        ).count()

        # Vols en cours d'occupation (arrivés mais pas encore partis)
        vols_en_cours = Vol.objects.filter(
            statut='ALLOUE',
            date_heure_debut_occupation__lte=now,
            date_heure_fin_occupation__gt=now
        ).count()

        # Prochain vol à allouer (utile pour la priorisation)
        prochain_vol = Vol.objects.filter(statut='ATTENTE').order_by('date_heure_debut_occupation').first()

        context['vol_stats'] = {
            'attente': vols_attente,
            'alloues_futurs': vols_alloues_futurs,
            'en_cours': vols_en_cours,
            'prochain_vol': prochain_vol,
        }

        # --- 3. Statistiques des Incidents ---

        context['incident_stats'] = Incident.objects.filter(
            statut__in=['OUVERT', 'ENCOURS']
        ).count()

        # Liste des 5 derniers incidents actifs
        context['derniers_incidents'] = Incident.objects.filter(
            statut__in=['OUVERT', 'ENCOURS']
        ).select_related('stand').order_by('-date_heure_declaration')[:5]

        return context




class LancerAllocationView(View):
    """Déclenche le service d'allocation des stands et redirige vers la liste des vols."""
    def post(self, request, *args, **kwargs):
        # On appelle le service d'allocation
        allocated, unallocated = allouer_stands_optimise()

        if allocated > 0:
            messages.success(request, f"🚀 {allocated} vol(s) ont été alloués avec succès.")
        if unallocated > 0:
            messages.warning(request, f"⚠️ {unallocated} vol(s) n'ont pas pu être alloués (conflit, dimensions ou stand indisponible).")
        if allocated == 0 and unallocated == 0:
             messages.info(request, "Aucun vol en statut 'ATTENTE' à traiter.")

        # Rediriger vers la liste des vols pour voir le résultat
        return redirect('vol_list')


def reallouer_vol_action(request, vol_pk):
    """
    Gère la demande de réallocation d'un seul vol.
    """
    if request.method != 'POST':
        messages.error(request, "Erreur de méthode.")
        return redirect('vol_detail', pk=vol_pk)

    # Appel du service de réallocation
    succes, message = reallouer_vol_unique(vol_pk)

    if succes:
        messages.success(request, message)
    else:
        messages.warning(request, message) # Warning si échec de la réallocation

    return redirect('vol_detail', pk=vol_pk)



