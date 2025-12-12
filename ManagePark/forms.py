from crispy_forms.helper import FormHelper
from django import forms
from .models import Vol, Avion, Incident, Stand
from django.utils import timezone


# Définition des champs pour la création de l'Avion dans le formulaire du Vol
class AvionForm(forms.ModelForm):
    """
    Formulaire pour les données de l'Avion.
    Il sera inclus dans la vue de création de Vol.
    """
    # Champ caché pour stocker l'ID de l'Avion si on en réutilise un existant
    avion_exist_pk = forms.CharField(required=False, widget=forms.HiddenInput())

    class Meta:
        model = Avion
        # Note: L'immatriculation doit être le premier champ pour la logique de recherche.
        fields = ['immatriculation', 'type', 'longueur', 'largeur', 'description']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
        }

    def clean_immatriculation(self):
        """
        Nettoie et standardise l'immatriculation, puis vérifie l'existence.
        """
        immatriculation = self.cleaned_data['immatriculation'].upper().strip()

        # Tente de trouver un avion existant avec cette immatriculation
        try:
            avion_exist = Avion.objects.get(immatriculation=immatriculation)

            # Si l'avion existe, nous stockons son PK dans le champ caché
            # et nous validons que les autres champs de l'avion ne sont pas modifiés.
            self.cleaned_data['avion_exist_pk'] = str(avion_exist.pk)

            # Si un avion est trouvé, seuls l'immatriculation et le type sont nécessaires.
            # On ignore la validation des autres champs pour ne pas forcer la réentrée des dimensions.
            # Cependant, dans une application réelle, on pourrait vouloir vérifier la cohérence.

        except Avion.DoesNotExist:
            # Si l'avion n'existe pas, nous nous assurons que tous les autres champs
            # (dimensions, type) sont remplis pour pouvoir créer un nouvel Avion.
            if not all(self.cleaned_data.get(f) for f in ['type', 'longueur', 'largeur']):
                raise forms.ValidationError(
                    "Si l'immatriculation est nouvelle, vous devez fournir le type, la longueur et la largeur de l'avion."
                )

        return immatriculation


# --- Formulaire pour le Vol ---
# Fichier : forms.py

class AvionForm(forms.ModelForm):
    # ... (Meta et autres champs) ...

    # Ajoutez un champ booléen pour indiquer si nous réutilisons un existant
    est_existant = forms.BooleanField(required=False, initial=False, widget=forms.HiddenInput())

    class Meta:
        model = Avion
        fields = ['immatriculation', 'type', 'longueur', 'largeur', 'description']

    def clean_immatriculation(self):
        immatriculation = self.cleaned_data['immatriculation'].upper().strip()

        try:
            avion_exist = Avion.objects.get(immatriculation=immatriculation)

            # SI L'AVION EXISTE, ENREGISTRER L'ÉTAT ET MARQUER LES AUTRES CHAMPS COMME NON REQUIS
            self.cleaned_data['est_existant'] = True

            # On rend les autres champs non requis pour le reste du processus de nettoyage
            self.fields['type'].required = False
            self.fields['longueur'].required = False
            self.fields['largeur'].required = False

        except Avion.DoesNotExist:
            self.cleaned_data['est_existant'] = False
            # SI L'AVION EST NOUVEAU, les champs restent requis

        return immatriculation

    # Ajoutez un nettoyage général pour vérifier que les champs sont remplis si l'avion est nouveau
    def clean(self):
        cleaned_data = super().clean()

        # Si 'est_existant' est False, nous vérifions que les champs requis pour la création sont là
        if not cleaned_data.get('est_existant'):
            if not all(cleaned_data.get(f) for f in ['type', 'longueur', 'largeur']):
                # Ce message d'erreur sera maintenant généré par AvionForm.is_valid() si c'est nouveau et incomplet.
                raise forms.ValidationError(
                    "Si l'immatriculation est nouvelle, vous devez fournir le type, la longueur et la largeur de l'avion."
                )
        return cleaned_data


class StandForm(forms.ModelForm):
    class Meta:
        model = Stand
        fields = [
            'nom_operationnel',
            'longueur',
            'largeur',
            'distance_stand_aerogare',
            'disponibilite'
        ]

        widgets = {
            'disponibilite': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        longueur = cleaned_data.get("longueur")
        largeur = cleaned_data.get("largeur")

        # Validation simple pour s'assurer que les dimensions sont positives
        if longueur is not None and longueur <= 0:
            self.add_error('longueur', "La longueur du stand doit être positive.")
        if largeur is not None and largeur <= 0:
            self.add_error('largeur', "La largeur du stand doit être positive.")

        return cleaned_data


class IncidentForm(forms.ModelForm):
    class Meta:
        model = Incident
        # Nous excluons 'stand' car il sera défini dans la vue
        fields = ['stand','type_incident', 'description']

        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
        }


class VolUpdateForm(forms.ModelForm):
    # Permet de modifier l'avion lié au vol (au cas où l'immatriculation change)
    avion = forms.ModelChoiceField(
        queryset=Avion.objects.all(),
        required=False,
        label="Avion (Immatriculation)",
        empty_label="--- Choisir un Avion ---"
    )

    class Meta:
        model = Vol
        fields = [
            'num_vol_arrive', 'num_vol_depart',
            'date_heure_debut_occupation', 'date_heure_fin_occupation', 'provenance',
            'destination', 'avion'
        ]

        # NOTE : Nous n'incluons pas 'statut' car il doit être géré par les services (Allocation)

        widgets = {
            'date_heure_debut_occupation': forms.DateTimeInput(
                attrs={
                    'type': 'datetime-local',
                    'class': 'form-control'
                },
                format='%Y-%m-%dT%H:%M'
            ),
            'date_heure_fin_occupation': forms.DateTimeInput(
                attrs={
                    'type': 'datetime-local',
                    'class': 'form-control'
                },
                format='%Y-%m-%dT%H:%M'
            ),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Formater les dates existantes pour l'affichage
        datetime_fields = ['date_heure_debut_occupation', 'date_heure_fin_occupation']
        for field in datetime_fields:
            if self.initial.get(field):
                if isinstance(self.initial[field], str):
                    # Si c'est déjà une string, la garder
                    continue
                # Convertir l'objet datetime en string formatée
                self.initial[field] = self.initial[field].strftime('%Y-%m-%dT%H:%M')

    def clean(self):
        cleaned_data = super().clean()
        return cleaned_data