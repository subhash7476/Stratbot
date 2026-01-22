"""
Machine Learning Module for Pattern Analysis

Provides advanced feature importance analysis using:
- Random Forest
- XGBoost
- SHAP values for explainability
"""

import numpy as np
import pandas as pd
from typing import Dict, Tuple, List
import warnings
warnings.filterwarnings('ignore')


def prepare_ml_dataset(positive_features: pd.DataFrame, 
                        negative_features: pd.DataFrame) -> Tuple[pd.DataFrame, pd.Series]:
    """
    Prepare dataset for ML training
    
    Returns:
    --------
    X : pd.DataFrame (features)
    y : pd.Series (labels: 1=pattern, 0=non-pattern)
    """
    # Add labels
    positive_features = positive_features.copy()
    negative_features = negative_features.copy()
    
    positive_features['label'] = 1
    negative_features['label'] = 0
    
    # Combine
    combined = pd.concat([positive_features, negative_features], ignore_index=True)
    
    # Drop non-feature columns
    feature_cols = [col for col in combined.columns 
                   if col not in ['timestamp', 'start_price', 'label']]
    
    X = combined[feature_cols]
    y = combined['label']
    
    # Handle missing values
    X = X.fillna(X.median())
    
    return X, y


def train_random_forest(X: pd.DataFrame, y: pd.Series, 
                        test_size: float = 0.2) -> Tuple[object, Dict]:
    """
    Train Random Forest and return model + metrics
    """
    try:
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import classification_report, confusion_matrix, roc_auc_score
    except ImportError:
        raise ImportError("sklearn not installed. Run: pip install scikit-learn")
    
    # Split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=42, stratify=y
    )
    
    # Train
    rf = RandomForestClassifier(
        n_estimators=100,
        max_depth=10,
        min_samples_split=20,
        min_samples_leaf=10,
        random_state=42,
        n_jobs=-1
    )
    
    rf.fit(X_train, y_train)
    
    # Evaluate
    y_pred = rf.predict(X_test)
    y_prob = rf.predict_proba(X_test)[:, 1]
    
    metrics = {
        'train_score': rf.score(X_train, y_train),
        'test_score': rf.score(X_test, y_test),
        'roc_auc': roc_auc_score(y_test, y_prob),
        'classification_report': classification_report(y_test, y_pred, output_dict=True),
        'confusion_matrix': confusion_matrix(y_test, y_pred),
        'feature_importance': pd.DataFrame({
            'feature': X.columns,
            'importance': rf.feature_importances_
        }).sort_values('importance', ascending=False)
    }
    
    return rf, metrics


def train_xgboost(X: pd.DataFrame, y: pd.Series, 
                  test_size: float = 0.2) -> Tuple[object, Dict]:
    """
    Train XGBoost and return model + metrics
    """
    try:
        import xgboost as xgb
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import classification_report, confusion_matrix, roc_auc_score
    except ImportError:
        raise ImportError("xgboost not installed. Run: pip install xgboost")
    
    # Split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=42, stratify=y
    )
    
    # Calculate scale_pos_weight for imbalanced classes
    scale_pos_weight = (y_train == 0).sum() / (y_train == 1).sum()
    
    # Train
    model = xgb.XGBClassifier(
        n_estimators=100,
        max_depth=6,
        learning_rate=0.1,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=scale_pos_weight,
        random_state=42,
        n_jobs=-1
    )
    
    model.fit(X_train, y_train)
    
    # Evaluate
    y_pred = model.predict(X_test)
    y_prob = model.predict_proba(X_test)[:, 1]
    
    metrics = {
        'train_score': model.score(X_train, y_train),
        'test_score': model.score(X_test, y_test),
        'roc_auc': roc_auc_score(y_test, y_prob),
        'classification_report': classification_report(y_test, y_pred, output_dict=True),
        'confusion_matrix': confusion_matrix(y_test, y_pred),
        'feature_importance': pd.DataFrame({
            'feature': X.columns,
            'importance': model.feature_importances_
        }).sort_values('importance', ascending=False)
    }
    
    return model, metrics


def compute_shap_values(model, X: pd.DataFrame, sample_size: int = 100) -> Tuple[object, np.ndarray]:
    """
    Compute SHAP values for model explainability
    
    Returns:
    --------
    explainer : shap.Explainer
    shap_values : np.ndarray
    """
    try:
        import shap
    except ImportError:
        raise ImportError("shap not installed. Run: pip install shap")
    
    # Sample data for faster computation
    if len(X) > sample_size:
        X_sample = X.sample(n=sample_size, random_state=42)
    else:
        X_sample = X
    
    # Create explainer
    explainer = shap.Explainer(model, X_sample)
    
    # Compute SHAP values
    shap_values = explainer(X_sample)
    
    return explainer, shap_values


def get_top_features_by_shap(shap_values, feature_names: List[str], top_n: int = 10) -> pd.DataFrame:
    """
    Get top features by mean absolute SHAP value
    """
    mean_abs_shap = np.abs(shap_values.values).mean(axis=0)
    
    importance_df = pd.DataFrame({
        'feature': feature_names,
        'mean_abs_shap': mean_abs_shap
    }).sort_values('mean_abs_shap', ascending=False).head(top_n)
    
    return importance_df


def cross_validate_model(X: pd.DataFrame, y: pd.Series, 
                         model_type: str = 'rf', n_splits: int = 5) -> Dict:
    """
    Perform cross-validation and return average metrics
    """
    try:
        from sklearn.model_selection import cross_val_score, StratifiedKFold
        from sklearn.ensemble import RandomForestClassifier
        import xgboost as xgb
    except ImportError:
        raise ImportError("Required libraries not installed")
    
    # Select model
    if model_type == 'rf':
        model = RandomForestClassifier(
            n_estimators=100,
            max_depth=10,
            random_state=42,
            n_jobs=-1
        )
    elif model_type == 'xgb':
        scale_pos_weight = (y == 0).sum() / (y == 1).sum()
        model = xgb.XGBClassifier(
            n_estimators=100,
            max_depth=6,
            scale_pos_weight=scale_pos_weight,
            random_state=42,
            n_jobs=-1
        )
    else:
        raise ValueError("model_type must be 'rf' or 'xgb'")
    
    # Cross-validation
    cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
    
    scores = {
        'accuracy': cross_val_score(model, X, y, cv=cv, scoring='accuracy'),
        'roc_auc': cross_val_score(model, X, y, cv=cv, scoring='roc_auc'),
        'precision': cross_val_score(model, X, y, cv=cv, scoring='precision'),
        'recall': cross_val_score(model, X, y, cv=cv, scoring='recall'),
        'f1': cross_val_score(model, X, y, cv=cv, scoring='f1')
    }
    
    results = {
        metric: {
            'mean': scores[metric].mean(),
            'std': scores[metric].std(),
            'scores': scores[metric]
        }
        for metric in scores
    }
    
    return results


def find_optimal_threshold(model, X_test: pd.DataFrame, y_test: pd.Series) -> Tuple[float, Dict]:
    """
    Find optimal classification threshold using ROC curve
    """
    try:
        from sklearn.metrics import roc_curve, precision_recall_curve
    except ImportError:
        raise ImportError("sklearn not installed")
    
    # Get probabilities
    y_prob = model.predict_proba(X_test)[:, 1]
    
    # ROC curve
    fpr, tpr, thresholds_roc = roc_curve(y_test, y_prob)
    
    # Find optimal threshold (maximize Youden's J statistic)
    j_scores = tpr - fpr
    optimal_idx = np.argmax(j_scores)
    optimal_threshold = thresholds_roc[optimal_idx]
    
    # Precision-Recall curve
    precision, recall, thresholds_pr = precision_recall_curve(y_test, y_prob)
    
    # F1 scores
    f1_scores = 2 * (precision * recall) / (precision + recall + 1e-10)
    optimal_f1_idx = np.argmax(f1_scores)
    optimal_f1_threshold = thresholds_pr[optimal_f1_idx] if optimal_f1_idx < len(thresholds_pr) else 0.5
    
    results = {
        'roc_optimal_threshold': optimal_threshold,
        'roc_optimal_tpr': tpr[optimal_idx],
        'roc_optimal_fpr': fpr[optimal_idx],
        'f1_optimal_threshold': optimal_f1_threshold,
        'f1_optimal_score': f1_scores[optimal_f1_idx],
        'roc_curve': (fpr, tpr, thresholds_roc),
        'pr_curve': (precision, recall, thresholds_pr)
    }
    
    return optimal_threshold, results


def feature_selection_recursive(X: pd.DataFrame, y: pd.Series, 
                                min_features: int = 5) -> List[str]:
    """
    Recursive Feature Elimination to select most important features
    """
    try:
        from sklearn.feature_selection import RFECV
        from sklearn.ensemble import RandomForestClassifier
    except ImportError:
        raise ImportError("sklearn not installed")
    
    model = RandomForestClassifier(n_estimators=50, random_state=42, n_jobs=-1)
    
    rfecv = RFECV(
        estimator=model,
        step=1,
        cv=5,
        scoring='roc_auc',
        min_features_to_select=min_features,
        n_jobs=-1
    )
    
    rfecv.fit(X, y)
    
    selected_features = X.columns[rfecv.support_].tolist()
    
    return selected_features


def get_feature_interactions(model, X: pd.DataFrame, top_n: int = 10) -> pd.DataFrame:
    """
    Identify potential feature interactions using tree-based model
    """
    try:
        from sklearn.ensemble import RandomForestClassifier
    except ImportError:
        raise ImportError("sklearn not installed")
    
    # Get feature importances
    if hasattr(model, 'feature_importances_'):
        importances = model.feature_importances_
    else:
        return pd.DataFrame()
    
    # Create interaction features (top features only)
    top_features = X.columns[np.argsort(importances)[-10:]].tolist()
    
    interactions = []
    for i, feat1 in enumerate(top_features):
        for feat2 in top_features[i+1:]:
            # Create interaction
            interaction_name = f"{feat1} × {feat2}"
            interaction_values = X[feat1] * X[feat2]
            
            # Correlation with each feature
            corr1 = np.corrcoef(X[feat1], interaction_values)[0, 1]
            corr2 = np.corrcoef(X[feat2], interaction_values)[0, 1]
            
            interactions.append({
                'interaction': interaction_name,
                'feature1': feat1,
                'feature2': feat2,
                'corr_with_feat1': corr1,
                'corr_with_feat2': corr2,
                'avg_corr': (abs(corr1) + abs(corr2)) / 2
            })
    
    interaction_df = pd.DataFrame(interactions).sort_values('avg_corr', ascending=False).head(top_n)
    
    return interaction_df


def analyze_pattern_timing(patterns_df: pd.DataFrame) -> Dict:
    """
    Analyze timing patterns - which hours/days have more patterns
    """
    patterns_df = patterns_df.copy()
    
    # Extract time features
    patterns_df['hour'] = patterns_df['timestamp'].dt.hour
    patterns_df['day_of_week'] = patterns_df['timestamp'].dt.dayofweek
    patterns_df['day_name'] = patterns_df['timestamp'].dt.day_name()
    patterns_df['month'] = patterns_df['timestamp'].dt.month
    
    analysis = {
        'hourly_distribution': patterns_df['hour'].value_counts().sort_index(),
        'daily_distribution': patterns_df['day_name'].value_counts(),
        'monthly_distribution': patterns_df['month'].value_counts().sort_index(),
        'best_hour': patterns_df['hour'].mode().values[0] if len(patterns_df) > 0 else None,
        'best_day': patterns_df['day_name'].mode().values[0] if len(patterns_df) > 0 else None,
    }
    
    return analysis


def generate_trading_rules(feature_importance_df: pd.DataFrame, 
                           positive_features: pd.DataFrame,
                           top_n: int = 5) -> List[str]:
    """
    Generate human-readable trading rules from top features
    """
    rules = []
    
    top_features = feature_importance_df.head(top_n)
    
    for _, row in top_features.iterrows():
        feature = row['feature']
        
        if feature in positive_features.columns:
            # Get median value for pattern cases
            median_value = positive_features[feature].median()
            percentile_75 = positive_features[feature].quantile(0.75)
            percentile_25 = positive_features[feature].quantile(0.25)
            
            # Generate rule
            if 'volume' in feature.lower():
                if median_value > 1.5:
                    rules.append(f"✓ {feature.replace('_', ' ').title()} should be > {median_value:.2f}x average")
            elif 'rsi' in feature.lower():
                rules.append(f"✓ {feature.upper()} should be between {percentile_25:.1f} and {percentile_75:.1f}")
            elif 'atr' in feature.lower():
                rules.append(f"✓ {feature.upper()} should be around {median_value:.2f}")
            elif 'pct' in feature.lower() or 'distance' in feature.lower():
                if abs(median_value) < 1:
                    rules.append(f"✓ {feature.replace('_', ' ').title()} should be near 0 (range: {percentile_25:.2f}% to {percentile_75:.2f}%)")
                else:
                    rules.append(f"✓ {feature.replace('_', ' ').title()} should be around {median_value:.2f}%")
            else:
                rules.append(f"✓ {feature.replace('_', ' ').title()}: target value ≈ {median_value:.2f}")
    
    return rules


if __name__ == "__main__":
    print("ML Pattern Analysis Module")
    print("Import this module in your Streamlit app for ML-based feature importance")