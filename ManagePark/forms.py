from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Row, Column, Field
from django import forms
from .models import Vol, Avion, Incident, Stand
from django.utils import timezone


# --- Formulaire pour le Vol ---


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


class AvionUpdateForm(forms.ModelForm):
    class Meta:
        model = Avion
        fields = ['immatriculation', 'type', 'description', 'longueur', 'largeur']


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
    date_heure_debut_occupation = forms.DateTimeField(
        label="Heure d'arrivée / Début d'occupation",
        required=False,
        # input_formats n'est généralement pas nécessaire pour datetime-local
        input_formats=['%Y-%m-%dT%H:%M'],  # Format HTML5 datetime-local
        widget=forms.DateTimeInput(
            attrs={
                'type': 'datetime-local',
                'class': 'form-control'
            },
            format='%Y-%m-%dT%H:%M'
        )
    )

    date_heure_fin_occupation = forms.DateTimeField(
        label="Heure de départ / Fin d'occupation",
        required=False,
        # input_formats n'est généralement pas nécessaire pour datetime-local
        input_formats=['%Y-%m-%dT%H:%M'],  # Format HTML5 datetime-local
        widget=forms.DateTimeInput(
            attrs={
                'type': 'datetime-local',
                'class': 'form-control'
            },
            format='%Y-%m-%dT%H:%M'
        )
    )

    class Meta:
        model = Vol
        fields = [
            'num_vol_arrive', 'num_vol_depart',
            'date_heure_debut_occupation', 'date_heure_fin_occupation',
            'provenance', 'destination', 'avion'
        ]

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
        """Validation personnalisée pour vérifier que la fin est après le début."""
        cleaned_data = super().clean()
        debut = cleaned_data.get('date_heure_debut_occupation')
        fin = cleaned_data.get('date_heure_fin_occupation')

        if debut and fin:
            if fin <= debut:
                raise forms.ValidationError(
                    "L'heure de départ doit être postérieure à l'heure d'arrivée."
                )

        return cleaned_data


from datetime import date, timedelta

class DateFilterForm(forms.Form):
    date_choisie = forms.DateField(
        label="Date",
        widget=forms.DateInput(attrs={
            'type': 'date',
            'class': 'form-control',
            'min': date.today().isoformat()
        }),
        required=True
    )
