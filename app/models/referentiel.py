"""
Référentiel des cultures et variétés : base agronomique du moteur de conseil.

Chaque culture porte son cycle et ses stades (pour le calendrier et les besoins en eau,
méthode FAO-56 : ETc = Kc x ET0), sa fumure recommandée et ses rendements de référence.
Toutes les valeurs sont modifiables par l'administrateur ; `a_valider` signale celles
qui doivent encore être confirmées par l'ISRA / le CDH.
"""
from datetime import datetime

from app.extensions import db


class CultureRef(db.Model):
    __tablename__ = "referentiel_cultures"

    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(60), unique=True, nullable=False)
    synonymes = db.Column(db.String(255))         # noms rencontrés dans les fichiers, séparés par des virgules
    famille = db.Column(db.String(40))            # Céréale, Légume-fruit, Légume-feuille, Racine / bulbe
    systeme = db.Column(db.String(20))            # Pluvial / Irrigué
    periode = db.Column(db.String(255))           # période de culture conseillée
    ecartements = db.Column(db.String(255))

    # Cycle et stades (jours) — FAO-56 tableau 11, ramenés à la durée de cycle locale
    cycle_j = db.Column(db.Integer)
    l_ini = db.Column(db.Integer)
    l_dev = db.Column(db.Integer)
    l_mid = db.Column(db.Integer)
    l_fin = db.Column(db.Integer)
    # Coefficients culturaux — FAO-56 tableau 12
    kc_ini = db.Column(db.Float)
    kc_mid = db.Column(db.Float)
    kc_fin = db.Column(db.Float)

    # Rendements de référence (t/ha)
    rdt_min = db.Column(db.Float)
    rdt_max = db.Column(db.Float)
    # Potentiel par défaut de la spéculation (t/ha) : appliqué aux parcelles dont la variété
    # est inconnue ou n'a pas de potentiel propre
    rdt_potentiel_t = db.Column(db.Float)

    # Fumure recommandée (par ha)
    fumure_org_min_t = db.Column(db.Float)
    fumure_org_max_t = db.Column(db.Float)
    engrais_fond = db.Column(db.String(40))       # formule, ex. 10-10-20
    fond_kg = db.Column(db.Float)
    engrais_couverture = db.Column(db.String(40))
    couverture_kg = db.Column(db.Float)           # par apport
    apports_j = db.Column(db.String(60))          # jours après semis/repiquage, ex. "15,30,50,80"

    ph_min = db.Column(db.Float)                  # en dessous : sol trop acide pour la culture
    nom_scientifique = db.Column(db.String(120))
    reference_j0 = db.Column(db.String(12))       # jours comptés depuis le « semis » ou le « repiquage »
    fiche_id = db.Column(db.Integer, db.ForeignKey("referentiel_fiches.id", ondelete="SET NULL",
                                                   use_alter=True, name="fk_culture_fiche"))
    source = db.Column(db.Text)
    a_valider = db.Column(db.Boolean, default=True)
    maj_le = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    varietes = db.relationship("VarieteRef", backref="culture", lazy="dynamic",
                               cascade="all, delete-orphan")
    fiche = db.relationship("FicheTechnique", foreign_keys=[fiche_id], post_update=True)
    itineraire = db.relationship("ItineraireRef", lazy="dynamic", cascade="all, delete-orphan",
                                 order_by="ItineraireRef.jour, ItineraireRef.ordre")
    ravageurs = db.relationship("RavageurRef", lazy="dynamic", cascade="all, delete-orphan",
                                order_by="RavageurRef.id")
    irrigation = db.relationship("IrrigationRef", lazy="dynamic", cascade="all, delete-orphan",
                                 order_by="IrrigationRef.systeme, IrrigationRef.phase")

    @property
    def jours_apports(self):
        out = []
        for x in (self.apports_j or "").replace(";", ",").split(","):
            try:
                out.append(int(x.strip()))
            except ValueError:
                pass
        return out


class VarieteRef(db.Model):
    __tablename__ = "referentiel_varietes"
    __table_args__ = (db.UniqueConstraint("culture_id", "nom", name="uq_variete_culture_nom"),)

    id = db.Column(db.Integer, primary_key=True)
    culture_id = db.Column(db.Integer, db.ForeignKey("referentiel_cultures.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    nom = db.Column(db.String(80), nullable=False)
    cycle_j = db.Column(db.Integer)
    rdt_potentiel_t = db.Column(db.Float)
    notes = db.Column(db.Text)
    source = db.Column(db.String(255))


class PotentielParcelle(db.Model):
    """Origine du rendement potentiel d'une parcelle (parcelles.rendement_potentiel) :
    variété, spéculation (valeur par défaut) ou saisie manuelle sur la fiche. Les saisies
    manuelles ne sont jamais écrasées par l'application automatique des potentiels."""
    __tablename__ = "parcelles_potentiel"

    parcelle_id = db.Column(db.Integer, db.ForeignKey("parcelles.id", ondelete="CASCADE"),
                            primary_key=True)
    origine = db.Column(db.String(12), nullable=False)       # variete / speculation / manuel
    culture_id = db.Column(db.Integer, db.ForeignKey("referentiel_cultures.id", ondelete="SET NULL"))
    variete_id = db.Column(db.Integer, db.ForeignKey("referentiel_varietes.id", ondelete="SET NULL"))
    variete = db.relationship("VarieteRef")
    culture = db.relationship("CultureRef")
    maj_le = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# --------------------------------------------------------------------------
# Fiches techniques (ISRA / ANCAR) et itinéraire technique de référence
# --------------------------------------------------------------------------
class FicheTechnique(db.Model):
    """Fiche technique déposée (Word, PDF ou image), son texte lu (directement ou par OCR)
    et les rubriques extraites, à valider avant d'alimenter le référentiel."""
    __tablename__ = "referentiel_fiches"

    id = db.Column(db.Integer, primary_key=True)
    culture_id = db.Column(db.Integer, db.ForeignKey("referentiel_cultures.id", ondelete="SET NULL"), index=True)
    culture = db.relationship("CultureRef", foreign_keys=[culture_id])
    titre = db.Column(db.String(200))
    fichier_nom = db.Column(db.String(255))          # nom d'origine
    fichier_chemin = db.Column(db.String(255))       # nom de stockage (dossier des fiches)
    taille_ko = db.Column(db.Integer)
    methode = db.Column(db.String(60))               # Word (lecture directe), OCR (image)…
    confiance_ocr = db.Column(db.Float)
    texte = db.Column(db.Text)
    extraction = db.Column(db.Text)                  # JSON des rubriques (modifié à la validation)
    statut = db.Column(db.String(20), default="À valider")   # À valider / Appliquée / Remplacée
    depose_par = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    depose_le = db.Column(db.DateTime, default=datetime.utcnow)
    valide_par = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    valide_le = db.Column(db.DateTime)


class ItineraireRef(db.Model):
    """Opération de l'itinéraire technique d'une culture, datée en jours depuis le semis ou
    le repiquage (négatif : avant). Sert au calendrier et au suivi des opérations par parcelle."""
    __tablename__ = "referentiel_itineraire"

    id = db.Column(db.Integer, primary_key=True)
    culture_id = db.Column(db.Integer, db.ForeignKey("referentiel_cultures.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    fiche_id = db.Column(db.Integer, db.ForeignKey("referentiel_fiches.id", ondelete="SET NULL"))
    ordre = db.Column(db.Integer)
    categorie = db.Column(db.String(30))             # Préparation, Fumure, Entretien, Irrigation, Récolte…
    libelle = db.Column(db.String(200), nullable=False)
    jour = db.Column(db.Integer, nullable=False)
    dose_kg_ha = db.Column(db.Float)
    produit = db.Column(db.String(80))
    details = db.Column(db.Text)
    optionnel = db.Column(db.Boolean, default=False)


class RavageurRef(db.Model):
    """Ennemi de la culture (ravageur, maladie, adventice) et traitements recommandés."""
    __tablename__ = "referentiel_ravageurs"

    id = db.Column(db.Integer, primary_key=True)
    culture_id = db.Column(db.Integer, db.ForeignKey("referentiel_cultures.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    fiche_id = db.Column(db.Integer, db.ForeignKey("referentiel_fiches.id", ondelete="SET NULL"))
    nom = db.Column(db.String(120), nullable=False)
    type = db.Column(db.String(20))                  # Ravageur / Maladie / Adventice
    precisions = db.Column(db.String(255))
    matieres_actives = db.Column(db.Text)            # séparées par des virgules
    traitement = db.Column(db.Text)                  # texte de la fiche (matières actives, méthodes)

    @property
    def liste_ma(self):
        return [x.strip() for x in (self.matieres_actives or "").split(",") if x.strip()]


class IrrigationRef(db.Model):
    """Dose d'irrigation journalière (l/m²/j) par système et par phase de la culture."""
    __tablename__ = "referentiel_irrigation"

    id = db.Column(db.Integer, primary_key=True)
    culture_id = db.Column(db.Integer, db.ForeignKey("referentiel_cultures.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    fiche_id = db.Column(db.Integer, db.ForeignKey("referentiel_fiches.id", ondelete="SET NULL"))
    systeme = db.Column(db.String(30), nullable=False)       # Gravitaire / Aspersion / Goutte-à-goutte
    phase = db.Column(db.Integer, nullable=False)
    libelle = db.Column(db.String(120))
    dose_debut = db.Column(db.Float)                          # l/m²/j en début de phase
    dose_fin = db.Column(db.Float)                            # l/m²/j en fin de phase


class OperationParcelle(db.Model):
    """Suivi, sur une parcelle et une campagne, d'une opération de l'itinéraire technique."""
    __tablename__ = "parcelles_operations"
    __table_args__ = (db.UniqueConstraint("parcelle_id", "annee", "itineraire_id", name="uq_operation_parcelle"),)

    id = db.Column(db.Integer, primary_key=True)
    parcelle_id = db.Column(db.Integer, db.ForeignKey("parcelles.id", ondelete="CASCADE"), nullable=False, index=True)
    annee = db.Column(db.Integer, nullable=False)
    itineraire_id = db.Column(db.Integer, db.ForeignKey("referentiel_itineraire.id", ondelete="CASCADE"))
    itineraire = db.relationship("ItineraireRef")
    date_realisee = db.Column(db.Date)
    quantite_kg = db.Column(db.Float)                # quantité réellement apportée sur la parcelle
    note = db.Column(db.String(255))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    maj_le = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
